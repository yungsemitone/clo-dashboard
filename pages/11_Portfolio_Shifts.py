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
from src.ui import (apply_chrome, page_intro, CHART_SEQ, GOLD, CLARET, GREEN, RED, INK_2,
                    EXIT_TYPE_COLORS)

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
page_intro("Portfolio Shifts",
           "Track what leveraged-loan desks bought, cut, and repriced—then separate likely repayments and refinancings from discretionary sales.",
           "🧭")
if data is None or data[0].empty:
    st.warning("No loan-level data yet. Run `python run_loan_pipeline.py`.")
    st.stop()
df, pos, hist, latest = data

FUND_LABEL = {f["ticker"]: f"{f['manager']} ({f['ticker']})" for f in LOAN_FUNDS}
st.warning(f"**Loan-fund proxy, not CLO holdings.** Seven registered loan funds run by related desks; "
           f"latest filings {pos.groupby('fund').report_date.max().min():%b %Y}–{pos.report_date.max():%b %Y}. "
           "Exit labels are inferred, not observed trades.")
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


tabs = st.tabs(["Desk read", "Loan moves", "Crowd flow", "Sectors & macro",
                "Opportunity screen", "CLO mechanics"])

# ---------------------------------------------------------------- Desk read
with tabs[0]:
    lab = label(latest)
    sells = lab[lab.action.isin(["Exit", "Trim"])]
    buys = lab[lab.action.isin(["Add", "Upsize"])]
    c1, c2, c3 = st.columns(3)
    c1.metric("Par reduced", f"-${-sells.par_change.sum()/1e6:,.0f}M")
    c2.metric("Par added", f"+${buys.par_change.sum()/1e6:,.0f}M")
    held = latest[latest.action.isin(["Hold", "Upsize", "Trim"])]
    c3.metric("Median mark change", f"{held.price_change.median():+.2f} pts")
    st.caption("Each fund's latest filing versus its prior filing; par changes include exits and trims.")

    st.subheader("Exits and trims: repayment, refi, or sale?")
    st.caption("Coarse labels inferred from price, maturity, and facility changes; the detailed reason remains below.")
    exits = sells.groupby("exit_type").agg(Trades=("obligor", "count"),
                                          **{"Par cut $M": ("par_change", lambda x: -x.sum() / 1e6)}).reset_index()
    exits["Par cut $M"] = exits["Par cut $M"].round(1)
    exits = exits.rename(columns={"exit_type": "Exit type"})
    e1, e2 = st.columns([1, 1.5])
    with e1:
        st.dataframe(exits, hide_index=True, width="stretch")
    with e2:
        fig = px.bar(exits.sort_values("Par cut $M"), x="Par cut $M", y="Exit type", orientation="h",
                     color="Exit type", color_discrete_map=EXIT_TYPE_COLORS, custom_data=["Trades"])
        fig.update_traces(hovertemplate="%{y}<br>%{x:.1f}M par · %{customdata[0]} trade(s)<extra></extra>")
        fig.update_layout(height=210, xaxis_title="Par reduced ($M)", yaxis_title="",
                          showlegend=False, margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(fig, width="stretch")

    st.subheader("Possible explanations for reductions (inferred)")
    r = sells.groupby("likely_reason").agg(trades=("obligor", "count"),
                                           par_sold_mm=("par_change", lambda s: -s.sum() / 1e6)).reset_index()
    fig = px.bar(r.sort_values("par_sold_mm"), x="par_sold_mm", y="likely_reason", orientation="h",
                 custom_data=["trades"], color_discrete_sequence=[CLARET])
    fig.update_traces(hovertemplate="%{y}<br>%{x:.1f}M par · %{customdata[0]} exit/trim(s)<extra></extra>")
    fig.update_layout(height=340, xaxis_title="Par reduced ($M, latest quarter)", yaxis_title="",
                      margin=dict(l=0, r=0, t=10, b=0))
    st.plotly_chart(fig, width="stretch")

    st.markdown("**Most broadly sold borrowers** · top 8 with ≥2 managers cutting")
    t = cons[cons.sellers >= 2].sort_values(["sellers", "net_par_change"], ascending=[False, True]).head(8)
    t = t[["obligor", "sellers", "buyers", "price_curr"]].copy()
    t["price_curr"] = t["price_curr"].round(1)
    st.dataframe(t.rename(columns={"obligor": "Borrower", "sellers": "Managers Selling",
                                   "buyers": "Managers Buying", "price_curr": "Latest Price ¢"}),
                 hide_index=True, width="stretch")
    with st.expander("Most broadly bought borrowers"):
        t = cons[cons.buyers >= 2].sort_values(["buyers", "net_par_change"], ascending=False).head(8)
        t = t[["obligor", "buyers", "sellers", "price_curr"]].copy()
        t["price_curr"] = t["price_curr"].round(1)
        st.dataframe(t.rename(columns={"obligor": "Borrower", "buyers": "Managers Buying",
                                       "sellers": "Managers Selling", "price_curr": "Latest Price ¢"}),
                     hide_index=True, width="stretch")

    with st.expander("Biggest markdowns on continuing positions"):
        md = lab[lab.action.isin(["Hold", "Trim", "Upsize"])].sort_values("price_change").head(12)
        md = md[["obligor", "manager", "price_prev", "price_curr", "price_change", "likely_reason"]].round(1)
        st.dataframe(md.rename(columns={"obligor": "Borrower", "manager": "Manager", "price_prev": "Before ¢",
                                       "price_curr": "Now ¢", "price_change": "Change ¢", "likely_reason": "Likely reason"}),
                     hide_index=True, width="stretch")

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
    coarse = v[v.exit_type != ""]
    if not coarse.empty:
        br = coarse.groupby("exit_type").agg(Trades=("obligor", "count"),
                                            **{"Par cut $M": ("par_change", lambda x: -x.sum() / 1e6)}).reset_index()
        br["Par cut $M"] = br["Par cut $M"].round(1)
        br = br.rename(columns={"exit_type": "Exit type"})
        fig = px.bar(br, x="Exit type", y="Par cut $M", color="Exit type",
                     color_discrete_map=EXIT_TYPE_COLORS, custom_data=["Trades"])
        fig.update_traces(hovertemplate="%{x}<br>%{y:.1f}M par · %{customdata[0]} trade(s)<extra></extra>")
        fig.update_layout(height=240, showlegend=False, margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(fig, width="stretch")
    show = v[["obligor", "action", "exit_type", "par_prev", "par_curr", "price_curr", "likely_reason", "why"]].copy()
    show["par_prev"] = (show.par_prev / 1e6).round(2)
    show["par_curr"] = (show.par_curr / 1e6).round(2)
    show["price_curr"] = show.price_curr.fillna(v.price_prev).round(1)
    show = show.rename(columns={"obligor": "Borrower", "action": "Action", "exit_type": "Exit type",
                                "par_prev": "Par Before $M", "par_curr": "Par After $M", "price_curr": "Price ¢",
                                "likely_reason": "Likely reason", "why": "Why"})
    st.dataframe(show, hide_index=True, width="stretch", height=460,
                 column_config={"Why": st.column_config.TextColumn(width="large")})
    st.download_button("Download CSV", v.to_csv(index=False), f"{fund}_changes.csv")

# ---------------------------------------------------------------- Consensus & momentum
with tabs[2]:
    st.caption("Compare borrowers across managers. Each fund uses its latest filing versus its prior one.")
    with st.expander("How to read this tab"):
        st.markdown("Positive net flow means more managers are adding than cutting. Price momentum is the average change in fund-reported marks, not a traded return.")
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
    with st.expander("Borrower flow table", expanded=True):
        view = t[["obligor", "buyers", "sellers", "price_curr", "mom_1q", "net_par_change"]].copy()
        view["price_curr"] = view.price_curr.round(1)
        view["mom_1q"] = view.mom_1q.round(1)
        view["net_par_change"] = (view.net_par_change / 1e6).round(1)
        st.dataframe(view.rename(columns={"obligor": "Borrower", "buyers": "Managers Buying", "sellers": "Managers Selling",
                                          "price_curr": "Price ¢", "mom_1q": "1Q Mark Change ¢", "net_par_change": "Net Par Change $M"}),
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
    sector_detail = mix.copy()
    latest_mix = mix[mix.report_date == mix.report_date.max()].nlargest(6, "weight")
    keep = set(latest_mix.sector)
    mix["Sector"] = mix.sector.where(mix.sector.isin(keep), "Other sectors")
    mix = mix.groupby(["report_date", "Sector"], as_index=False).weight.sum()
    st.caption("Latest six sectors shown separately; remaining sectors grouped as Other.")
    fig = px.area(mix, x="report_date", y="weight", color="Sector", color_discrete_sequence=CHART_SEQ)
    fig.update_layout(height=420, yaxis_title="% of loan MV", xaxis_title="", margin=dict(l=0, r=0, t=10, b=0))
    st.plotly_chart(fig, width="stretch")
    d = sorted(mix.report_date.unique())
    if len(d) >= 2:
        w = sector_detail.pivot_table(index="sector", columns="report_date", values="weight", fill_value=0)
        chg = pd.DataFrame({"weight_first": w[d[0]], "weight_latest": w[d[-1]]})
        chg["change_pts"] = chg.weight_latest - chg.weight_first
        chg["sector_mark_change_latest_qtr"] = chg.index.map(sector_moves)
        chg["macro drivers"] = chg.index.map(lambda s: ", ".join(FRED_SERIES.get(x, x) for x in ls.SECTOR_DRIVERS.get(s, ([], ""))[0]))
        chg["how macro transmits"] = chg.index.map(lambda s: ls.SECTOR_DRIVERS.get(s, ([], ""))[1])
        st.markdown(f"**Sector weight shift {pd.Timestamp(d[0]):%b %Y} → {pd.Timestamp(d[-1]):%b %Y}** "
                    "(positive = manager leaning in)")
        view = chg[["weight_first", "weight_latest", "change_pts"]].sort_values("change_pts").round(1)
        view.columns = [f"Weight {pd.Timestamp(d[0]):%b %Y} %", f"Weight {pd.Timestamp(d[-1]):%b %Y} %", "Change (pts)"]
        st.dataframe(view, width="stretch")

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
        view = mc[["series", "start", "end", "change", "pct_change"]].round(2).rename(columns={
            "series": "Macro Series", "start": "Start", "end": "End", "change": "Change", "pct_change": "Change %"})
        st.dataframe(view, hide_index=True, width="stretch")
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
    st.caption("A diligence shortlist of discounted loans with signs of technical—not necessarily fundamental—selling. Not investment advice.")
    with st.expander("How the technical score works"):
        st.markdown("+2 price in the 70–85 constraint band · +1 per manager selling (max 3) · +1 sector peers not falling · +1 no default/PIK flag · +1 someone is buying · −2 for a >10-point quarterly drop. Ratings and borrower financials are not in these filings.")
    c1, c2, c3 = st.columns(3)
    lo, hi = c1.slider("Price band", 40, 100, (60, 92))
    min_score = c2.slider("Min technical score", 0, 8, 4)
    sectors = c3.multiselect("Sectors", sorted(pos.sector.unique()))
    op = ls.opportunity_screen(pos, latest, lo, hi)
    op = op[op.technical_score >= min_score]
    if sectors:
        op = op[op.sector.isin(sectors)]
    view = op[["obligor", "technical_score", "signal_read", "price", "sellers", "buyers"]].copy()
    view["price"] = view.price.round(1)
    view["Primary signal"] = view.signal_read.str.split(";").str[0]
    st.dataframe(view.drop(columns="signal_read").rename(columns={
        "obligor": "Borrower", "technical_score": "Technical Score", "price": "Price ¢",
        "sellers": "Sellers", "buyers": "Buyers"}),
        hide_index=True, width="stretch", height=460,
        column_config={"Primary signal": st.column_config.TextColumn(width="large")})
    if not op.empty:
        with st.expander("Full opportunity rationale"):
            selected_signal = st.selectbox("Borrower", op.obligor.tolist(), key="opportunity_rationale")
            signal = op.loc[op.obligor == selected_signal].iloc[0].signal_read
            st.markdown(signal)
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
