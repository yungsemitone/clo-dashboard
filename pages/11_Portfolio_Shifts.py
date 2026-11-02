import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import yaml
from sqlalchemy import create_engine

from src.analytics import loan_signals as ls
from src.scrapers.fred import FRED_SERIES
from src.scrapers.loan_nport_scraper import LOAN_FUNDS
from src.ui import apply_chrome, CHART_SEQ, GOLD, CLARET, GREEN, RED, INK_2

st.set_page_config(page_title="Portfolio Shifts & Signals", page_icon="🧭", layout="wide")

from src.auth import check_password
if not check_password():
    st.stop()
apply_chrome()

ROOT = Path(__file__).parent.parent
DB = ROOT / yaml.safe_load(open(ROOT / "config.yaml"))["database"]["path"]


@st.cache_resource
def engine():
    return create_engine(f"sqlite:///{DB}")


@st.cache_data(ttl=3600)
def load():
    eng = engine()
    with eng.connect() as c:
        tables = set(pd.read_sql("select name from sqlite_master where type='table'", c).name)
        if "loan_holdings" not in tables:
            return None
        df = ls.load_loans(c)
    pos = ls.obligor_positions(df)
    hist = ls.all_changes(pos)
    latest = ls.latest_changes(pos)
    return df, pos, hist, latest


data = load()
st.title("🧭 Portfolio Shifts & Signals")
st.caption("Loan-level view of what CLO managers' loan desks are adding, selling and re-marking — "
           "and the CLO mechanics that most likely forced the trade.")
if data is None or data[0].empty:
    st.warning("No loan-level data yet. Run `python run_loan_pipeline.py`.")
    st.stop()
df, pos, hist, latest = data

FUND_LABEL = {f["ticker"]: f"{f['manager']} ({f['ticker']})" for f in LOAN_FUNDS}
snap = pos.groupby("fund").report_date.agg(["min", "max", "nunique"])
with st.expander("ℹ️ Where this data comes from (and what's missing)", expanded=False):
    st.markdown(f"""
**Source — real SEC filings only.** CLO loan tapes (trustee reports) aren't on EDGAR, and 144A CLOs don't
file 10-D/ABS-15G. The closest public, loan-level, quarter-by-quarter view of a CLO manager's book is the
**NPORT-P of the registered loan funds run by the same leveraged-loan desk**. We track
{len(LOAN_FUNDS)} of them: {", ".join(FUND_LABEL.values())}. Each position gives borrower, facility, **par**,
**market value** (→ price vs par), maturity, coupon and **default / PIK flags**. Industries come from each fund's
own N-CSR schedule of investments, mapped to common macro sectors. Macro series are from FRED.

**Not publicly available (so inferred, and labelled as such):** Moody's/S&P ratings, individual CLO OC/IC
cushions, CCC bucket %, WARF/diversity, reinvestment-period end dates. "Likely reason" labels translate
price, flags, maturity and breadth of selling into the CLO constraint that most plausibly drove the trade.
Exits can be sales **or repayments** — exits at ~par are labelled as likely repaid/refi.

**Coverage now:** {pos.obligor_key.nunique():,} borrowers, {len(df):,} facility-snapshots,
{int(snap['nunique'].sum())} fund-quarters; latest snapshots {snap['max'].min():%b %Y}–{snap['max'].max():%b %Y}.
Industry classified for {pos[pos.sector!='Unclassified'].mv.sum()/pos.mv.sum()*100:.0f}% of market value.
""")
    st.dataframe(snap.rename(columns={"min": "first snapshot", "max": "latest snapshot", "nunique": "quarters"})
                 .rename(index=FUND_LABEL), width="stretch")

sector_moves = ls.sector_price_moves(latest).set_index("sector")["median_px_change"].to_dict()
cons = ls.consensus(latest)
sellers_by_key = cons.set_index("obligor_key")["sellers"].to_dict()


def label(frame):
    out = frame.apply(lambda r: ls.likely_reason(r.to_dict(), sector_moves,
                                                 sellers_by_key.get(r.obligor_key, 0)), axis=1)
    frame = frame.copy()
    frame["likely_reason"] = [o[0] for o in out]
    frame["why"] = [o[1] for o in out]
    return frame


tabs = st.tabs(["Desk read", "Position changes", "Consensus & momentum", "Industry mix & macro",
                "Opportunity screen", "How CLO traders think"])

# ---------------------------------------------------------------- Desk read
with tabs[0]:
    lab = label(latest)
    sells = lab[lab.action.isin(["Exit", "Trim"])]
    buys = lab[lab.action.isin(["Add", "Upsize"])]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Sells (exits + trims)", f"{len(sells)}", f"${-sells.par_change.sum()/1e6:,.0f}mm par out")
    c2.metric("Buys (adds + upsizes)", f"{len(buys)}", f"${buys.par_change.sum()/1e6:,.0f}mm par in")
    held = latest[latest.action.isin(["Hold", "Upsize", "Trim"])]
    c3.metric("Median mark change (held)", f"{held.price_change.median():+.2f} pts")
    sub80 = pos[pos.report_date == pos.groupby("fund").report_date.transform("max")]
    c4.metric("Positions marked < 80", f"{(sub80.price < 80).sum()}",
              f"{sub80[sub80.price < 80].mv.sum()/sub80.mv.sum()*100:.1f}% of MV", delta_color="off")

    st.subheader("Why they sold — latest quarter, by likely mechanical reason")
    r = sells.groupby("likely_reason").agg(trades=("obligor", "count"),
                                           par_sold_mm=("par_change", lambda s: -s.sum() / 1e6)).reset_index()
    fig = px.bar(r.sort_values("par_sold_mm"), x="par_sold_mm", y="likely_reason", orientation="h",
                 text="trades", color_discrete_sequence=[CLARET])
    fig.update_layout(height=340, xaxis_title="Par sold ($mm, latest quarter)", yaxis_title="",
                      margin=dict(l=0, r=0, t=10, b=0))
    st.plotly_chart(fig, width="stretch")

    a, b = st.columns(2)
    with a:
        st.markdown("**Most broadly sold borrowers** (≥2 managers cutting)")
        t = cons[cons.sellers >= 2].sort_values(["sellers", "net_par_change"], ascending=[False, True]).head(12)
        st.dataframe(t[["obligor", "sector", "sellers", "buyers", "seller_list", "price_prev", "price_curr"]]
                     .round(2), hide_index=True, width="stretch")
    with b:
        st.markdown("**Most broadly bought borrowers** (≥2 managers adding)")
        t = cons[cons.buyers >= 2].sort_values(["buyers", "net_par_change"], ascending=False).head(12)
        st.dataframe(t[["obligor", "sector", "buyers", "sellers", "buyer_list", "price_prev", "price_curr"]]
                     .round(2), hide_index=True, width="stretch")

    st.markdown("**Biggest markdowns on continuing positions**")
    md = lab[lab.action.isin(["Hold", "Trim", "Upsize"])].sort_values("price_change").head(12)
    st.dataframe(md[["obligor", "manager", "sector", "price_prev", "price_curr", "price_change", "action",
                     "likely_reason"]].round(2), hide_index=True, width="stretch")

# ---------------------------------------------------------------- Position changes
with tabs[1]:
    c1, c2 = st.columns([2, 2])
    fund = c1.selectbox("Manager (loan fund)", list(FUND_LABEL), format_func=FUND_LABEL.get)
    dates = ls.snapshot_dates(pos, fund)
    pairs = [(dates[i - 1], dates[i]) for i in range(len(dates) - 1, 0, -1)]
    pair = c2.selectbox("Period", pairs, format_func=lambda p: f"{p[0]:%b %Y} → {p[1]:%b %Y}")
    ch = label(ls.position_changes(pos, fund, *pair))
    acts = st.multiselect("Actions", ["Add", "Exit", "Upsize", "Trim", "Hold"], ["Add", "Exit", "Upsize", "Trim"])
    v = ch[ch.action.isin(acts)].sort_values("par_change")
    k = st.columns(5)
    for i, a in enumerate(["Add", "Exit", "Upsize", "Trim", "Hold"]):
        k[i].metric(a, int((ch.action == a).sum()))
    st.dataframe(v[["obligor", "sector", "industry", "action", "par_prev", "par_curr", "price_prev", "price_curr",
                    "price_change", "likely_reason", "why"]].round(2), hide_index=True, width="stretch",
                 height=520)
    st.download_button("Download CSV", v.to_csv(index=False), f"{fund}_changes.csv")

# ---------------------------------------------------------------- Consensus & momentum
with tabs[2]:
    st.markdown("Each manager's **latest quarter vs its prior quarter** (funds report on different month-ends, "
                "so 'same quarter' means each fund's most recent filing).")
    mom = ls.price_momentum(pos)
    cm = cons.merge(mom, on="obligor_key", how="left")
    c1, c2 = st.columns(2)
    min_n = c1.slider("Min managers involved (buyers + sellers)", 1, 6, 2)
    view = c2.radio("Show", ["Net sellers", "Net buyers", "All"], horizontal=True)
    t = cm[(cm.buyers + cm.sellers) >= min_n]
    if view == "Net sellers":
        t = t[t.net < 0].sort_values("net")
    elif view == "Net buyers":
        t = t[t.net > 0].sort_values("net", ascending=False)
    st.dataframe(t[["obligor", "sector", "holders_prev", "holders_curr", "buyers", "sellers", "buyer_list",
                    "seller_list", "price_curr", "mom_1q", "mom_2q", "net_par_change"]].round(2),
                 hide_index=True, width="stretch", height=420)
    st.markdown("**Price momentum vs crowd flow** — bottom-left = falling and being sold (forced-seller zone)")
    t2 = cm.dropna(subset=["mom_1q", "price_curr"])
    t2 = t2[(t2.buyers + t2.sellers) > 0]
    fig = px.scatter(t2, x="net", y="mom_1q", color="price_curr", hover_name="obligor",
                     color_continuous_scale=[RED, GOLD, GREEN], range_color=[70, 100],
                     labels={"net": "Net managers buying (− = selling)", "mom_1q": "Avg price change, last qtr (pts)",
                             "price_curr": "Price"})
    fig.update_layout(height=420, margin=dict(l=0, r=0, t=10, b=0))
    st.plotly_chart(fig, width="stretch")

# ---------------------------------------------------------------- Industry & macro
with tabs[3]:
    fund2 = st.selectbox("Manager", list(FUND_LABEL), format_func=FUND_LABEL.get, key="mix_fund")
    mix = ls.industry_mix(pos, fund2)
    fig = px.area(mix, x="report_date", y="weight", color="sector", color_discrete_sequence=px.colors.qualitative.Set3)
    fig.update_layout(height=420, yaxis_title="% of loan MV", xaxis_title="", margin=dict(l=0, r=0, t=10, b=0))
    st.plotly_chart(fig, width="stretch")
    d = sorted(mix.report_date.unique())
    if len(d) >= 2:
        w = mix.pivot_table(index="sector", columns="report_date", values="weight", fill_value=0)
        chg = pd.DataFrame({"weight_first": w[d[0]], "weight_latest": w[d[-1]]})
        chg["change_pts"] = chg.weight_latest - chg.weight_first
        chg["sector_mark_change_latest_qtr"] = chg.index.map(sector_moves)
        chg["macro drivers"] = chg.index.map(lambda s: ", ".join(FRED_SERIES.get(x, x) for x in ls.SECTOR_DRIVERS.get(s, ([], ""))[0]))
        chg["how macro transmits"] = chg.index.map(lambda s: ls.SECTOR_DRIVERS.get(s, ([], ""))[1])
        st.markdown(f"**Sector weight shift {pd.Timestamp(d[0]):%b %Y} → {pd.Timestamp(d[-1]):%b %Y}** "
                    "(positive = manager leaning in)")
        st.dataframe(chg.sort_values("change_pts").round(2), width="stretch")

    st.markdown("**All managers — latest sector weights** (where the CLO-manager crowd is concentrated)")
    allmix = ls.industry_mix(pos)
    lastd = allmix.groupby("fund").report_date.transform("max")
    hm = allmix[allmix.report_date == lastd].pivot_table(index="sector", columns="fund", values="weight", fill_value=0)
    hm.columns = [FUND_LABEL.get(c, c) for c in hm.columns]
    fig = px.imshow(hm.round(1), text_auto=True, aspect="auto", color_continuous_scale=["#251D19", GOLD])
    fig.update_layout(height=520, margin=dict(l=0, r=0, t=10, b=0))
    st.plotly_chart(fig, width="stretch")

    st.markdown("**Macro overlay (FRED)** — change over the latest reporting window")
    start = pd.Timestamp(min(ls.snapshot_dates(pos, f["ticker"])[-2] for f in LOAN_FUNDS))
    end = pd.Timestamp(max(snap["max"]))
    with engine().connect() as c:
        mc = ls.macro_changes(c, start, end)
        ms = pd.read_sql("select series_id, obs_date, value from macro_series", c, parse_dates=["obs_date"])
    if not mc.empty:
        mc["series"] = mc.series_id.map(FRED_SERIES)
        st.dataframe(mc[["series", "start", "end", "change", "pct_change"]].round(2), hide_index=True,
                     width="stretch")
        pick = st.multiselect("Plot series", list(FRED_SERIES), ["BAMLH0A3HYC", "BAMLH0A0HYM2"],
                              format_func=FRED_SERIES.get)
        if pick:
            fig = px.line(ms[ms.series_id.isin(pick)].assign(series=lambda x: x.series_id.map(FRED_SERIES)),
                          x="obs_date", y="value", color="series", color_discrete_sequence=CHART_SEQ)
            for dd in sorted(pos.report_date.unique()):
                fig.add_vline(x=pd.Timestamp(dd), line_width=0.5, line_dash="dot", line_color=INK_2)
            fig.update_layout(height=360, margin=dict(l=0, r=0, t=10, b=0), xaxis_title="", yaxis_title="")
            st.plotly_chart(fig, width="stretch")
            st.caption("Dotted lines = filing snapshot dates. CCC OAS widening vs BB is the clearest read on "
                       "how hard CLO CCC-bucket selling is hitting the market.")

# ---------------------------------------------------------------- Opportunity screen
with tabs[4]:
    st.markdown("""Loans whose discount looks **technical** (forced/constraint selling) rather than fundamental.
Score = +2 price in the 70–85 CLO-constraint band · +1 per manager selling (max 3) · +1 sector peers not falling ·
+1 no default/PIK flag · +1 someone is buying · −2 if it gapped down >10 pts last quarter. **This is a starting
list for diligence, not a recommendation** — ratings and borrower financials aren't in these filings.""")
    c1, c2, c3 = st.columns(3)
    lo, hi = c1.slider("Price band", 40, 100, (60, 92))
    min_score = c2.slider("Min technical score", 0, 8, 4)
    sectors = c3.multiselect("Sectors", sorted(pos.sector.unique()))
    op = ls.opportunity_screen(pos, latest, lo, hi)
    op = op[op.technical_score >= min_score]
    if sectors:
        op = op[op.sector.isin(sectors)]
    st.dataframe(op[["obligor", "sector", "industry", "price", "discount_to_par", "mom_1q", "mom_2q", "holders",
                     "sellers", "buyers", "coupon", "maturity", "technical_score", "signal_read"]].round(2),
                 hide_index=True, width="stretch", height=460,
                 column_config={"maturity": st.column_config.DateColumn(format="MMM YYYY")})
    from src.summarizer import ai_summaries_enabled, generate_trade_rationale
    if ai_summaries_enabled() and not op.empty:
        who = st.selectbox("AI trader's read on…", op.obligor.head(30).tolist())
        if st.button("Generate rationale"):
            r = op[op.obligor == who].iloc[0]
            ctx = {k: (round(v, 2) if isinstance(v, float) else v) for k, v in r[[
                "obligor", "sector", "industry", "price", "mom_1q", "mom_2q", "holders", "sellers", "buyers",
                "seller_list", "buyer_list", "coupon", "maturity", "sector_move", "is_default", "is_pik",
                "signal_read"]].items()}
            with st.spinner("Thinking like a CLO trader…"):
                txt = generate_trade_rationale(ctx)
            st.info(txt or "AI unavailable — use the rule-based read above.")
    else:
        st.caption("Set ANTHROPIC_API_KEY to enable a short AI trader's rationale per loan.")

# ---------------------------------------------------------------- Explainer
with tabs[5]:
    st.markdown("""
### Why CLO managers buy, sell and mark loans the way they do
A CLO is a box of ~200–400 broadly-syndicated loans funded by AAA→equity tranches. The manager is
graded not on price but on **par** and a set of indenture tests — so most trading is about *protecting
the tests*, not about a view that a loan is cheap or rich.

| Constraint | What it says | What it makes traders do | Signal here |
|---|---|---|---|
| **CCC bucket (~7.5%)** | Caa/CCC loans above 7.5% of par are carried at **market value**, not par, in the OC test | Sell downgraded / about-to-be-downgraded loans *regardless of price*; avoid names on negative watch | Price < 85, consensus selling |
| **Discount obligations (< ~80–85)** | Loans *bought* below ~80 are carried at purchase price until they recover | CLOs rarely buy sub-80 paper → thin bid → prices overshoot | Price < 80 |
| **OC / IC tests** | If par coverage breaches, cash is diverted from equity to pay down AAAs | Build par (buy at discount, sell at par), dump haircut assets | Sector stress, CCC selling |
| **Defaulted obligations** | Carried at lower of MV / recovery rate | Sell or work out; PIK toggles can breach eligibility | Default/PIK flag |
| **WARF / diversity / WAS** | Max average rating factor, min diversity, min spread | Sell tight-spread par loans to buy wider ones; cap sector concentration | Exits at ~par (rotation), sector weight shifts |
| **Reinvestment period end** | After it ends, prepayments mostly amortize the notes | Fewer buyers for long-dated / lower-rated loans; less par building | Industry-wide flows |
| **WAL test / maturity** | Max weighted-average life | Avoid long loans in old CLOs, roll short paper; amend-&-extends | Near-maturity, facility swaps |

**Reading a price drop.** If many managers cut a still-performing loan from the low-80s at the same time
while the sector is flat, that's often a **rating-driven** technical: CLOs (≈70% of loan demand) are forced
sellers and few have CCC room to buy. Non-CLO buyers (hedge funds, BDCs, private credit, distressed funds)
step in at a discount. That's the opportunity set the screen targets — *if* the fundamentals (leverage,
liquidity runway, sponsor, LME risk, document protections) hold up on diligence.

**Reading a markdown with a sector move.** Same-sector names falling together (e.g. software on AI
disruption fears, healthcare providers on reimbursement) is a macro repricing — check the macro tab.

**Reading exits at par.** Most exits at ~100 are repayments, refinancings or rotation into wider new issue
(spread/WAS management), not credit calls.
""")
