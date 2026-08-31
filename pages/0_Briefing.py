import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import pandas as pd

from src.db import init_db, get_session
from src.ui import apply_chrome
from src.analytics.conviction import latest_holdings
from src.analytics.pricing import valid_prices
from src.analytics.manager_scorecard import (
    tracked_positions, manager_trajectories, period_label,
)
from src.analytics.position_changes import get_filing_dates, compute_position_changes

import yaml
from pathlib import Path

st.set_page_config(page_title="Briefing", page_icon="📰", layout="wide")

from src.auth import check_password
if not check_password():
    st.stop()
apply_chrome()


@st.cache_resource
def get_db():
    config_path = Path(__file__).parent.parent / "config.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)
    init_db(config)
    return get_session(config)


FUND_NAMES = {"OXLC": "Oxford Lane", "ECC": "Eagle Point",
              "OCCI": "OFS Credit", "PDCC": "Pearl Diver", "PRIF": "Priority Income"}

session = get_db()

st.title("📰 Briefing")
st.caption("What the latest filings say — the read, before the charts")

hold = latest_holdings(session)
pos = tracked_positions(session)
if hold.empty:
    st.warning("No data available.")
    st.stop()

# ---------------------------------------------------------------- headline
priced = valid_prices(hold)
total_par, total_mv = priced["par"].sum(), priced["market_value"].sum()
avg_price = total_mv / total_par * 100 if total_par else 0
overall_delta = ((pos["par_then"] * pos["delta"]).sum() / pos["par_then"].sum()) if not pos.empty else 0
pct_down = (pos["delta"] < 0).mean() * 100 if not pos.empty else 0

# cross-held deals
fund_counts = hold.groupby("deal_id")["fund"].nunique()
cross_ids = fund_counts[fund_counts >= 2].index
cross = hold[hold["deal_id"].isin(cross_ids)]
cross_priced = valid_prices(cross)
cross_priced = cross_priced[cross_priced["price"] >= 1]

spreads = cross_priced.groupby(["deal_id", "deal_name", "manager"]).agg(
    funds=("fund", "nunique"), lo=("price", "min"), hi=("price", "max"),
    par=("par", "sum"), cusips=("cusip", lambda s: len({c for c in s if c})),
).reset_index()
spreads = spreads[spreads["funds"] >= 2]
spreads["spread"] = spreads["hi"] - spreads["lo"]
spreads = spreads.sort_values("spread", ascending=False)

latest_date = max(hold["fund"].map(
    lambda f: max(get_filing_dates(session, f)) if get_filing_dates(session, f) else None
).dropna())

m1, m2, m3, m4 = st.columns(4)
m1.metric("Portfolio Avg Mark", f"{avg_price:.1f}¢")
m2.metric("12-Mo Mark Change", f"{overall_delta:+.1f}¢")
m3.metric("Deals Held by 2+ Funds", len(cross_ids),
          help=f"{len(spreads)} of them have comparable prices on both sides.")
m4.metric("Most Recent Filing", f"{latest_date:%b %d, %Y}")

# ---------------------------------------------------------------- the read
worst = manager_trajectories(session, min_positions=3, positions=pos)
lead_lines = []
lead_lines.append(
    f"Across the five funds' latest filings, CLO positions are marked at an average "
    f"<b>{avg_price:.0f}¢</b> on the dollar of par."
)
if not pos.empty:
    lead_lines.append(
        f"Over the trailing window ({period_label(pos)}), the funds marked these books "
        f"<b>{overall_delta:+.1f}¢</b> on a par-weighted basis, with <b>{pct_down:.0f}%</b> of "
        f"individual positions written lower — a move in the asset class, not a few names."
    )
if not spreads.empty:
    top = spreads.iloc[0]
    lead_lines.append(
        f"The widest disagreement is <b>{top['deal_name']}</b>, where funds mark the same deal "
        f"{top['lo']:.0f}¢ to {top['hi']:.0f}¢ — a <b>{top['spread']:.0f}¢</b> gap."
    )

st.markdown(
    '<div style="background:#251D19;border-left:4px solid #CBA255;padding:1.1rem 1.3rem;'
    'border-radius:0 6px 6px 0;line-height:1.75;color:#C9BCAF;font-size:1.02rem;">'
    + " ".join(lead_lines) + "</div>", unsafe_allow_html=True)

st.divider()

# ---------------------------------------------------------------- disagreements
st.subheader("Where the funds disagree most")
st.caption("Same CLO, different marks. A shared CUSIP means the identical security — a genuine "
           "difference of opinion. Multiple CUSIPs mean the funds hold different tranches.")

if spreads.empty:
    st.info("No cross-held deals with comparable prices.")
else:
    d = spreads.head(8).copy()
    d["Case"] = d["cusips"].map(lambda n: "Same security" if n <= 1 else "Different tranches")
    d["Range"] = d.apply(lambda r: f"{r['lo']:.0f}¢ → {r['hi']:.0f}¢", axis=1)
    d["Held by"] = d["deal_id"].map(
        lambda i: ", ".join(sorted(cross[cross["deal_id"] == i]["fund"].unique())))
    show = d[["deal_name", "manager", "Held by", "Range", "spread", "Case"]].rename(
        columns={"deal_name": "Deal", "manager": "Manager", "spread": "Gap (¢)"})
    show["Gap (¢)"] = show["Gap (¢)"].round(0)
    st.dataframe(show, use_container_width=True, hide_index=True)
    st.caption("Full reasoning for any deal is on the **Cross-Fund Comparison** page.")

st.divider()

# ---------------------------------------------------------------- movers
left, right = st.columns(2)
with left:
    st.subheader("Marked down hardest")
    if worst.empty:
        st.info("Not enough history.")
    else:
        w = worst.tail(8).sort_values("delta")[["manager", "n_positions", "price_then", "price_now", "delta"]].copy()
        for c in ("price_then", "price_now", "delta"):
            w[c] = w[c].round(1)
        st.dataframe(w.rename(columns={
            "manager": "Manager", "n_positions": "Pos.", "price_then": "Was (¢)",
            "price_now": "Now (¢)", "delta": "Δ (¢)"}), use_container_width=True, hide_index=True)

with right:
    st.subheader("Held up best")
    if worst.empty:
        st.info("Not enough history.")
    else:
        b = worst.head(8)[["manager", "n_positions", "price_then", "price_now", "delta"]].copy()
        for c in ("price_then", "price_now", "delta"):
            b[c] = b[c].round(1)
        st.dataframe(b.rename(columns={
            "manager": "Manager", "n_positions": "Pos.", "price_then": "Was (¢)",
            "price_now": "Now (¢)", "delta": "Δ (¢)"}), use_container_width=True, hide_index=True)

st.divider()

# ---------------------------------------------------------------- positioning
st.subheader("Latest quarter positioning")
st.caption("Each fund's newest filing vs. the one before it.")

rows = []
for fund in sorted(FUND_NAMES):
    dates = get_filing_dates(session, fund)
    if len(dates) < 2:
        continue
    r = compute_position_changes(session, fund, dates[1], dates[0])["summary"]
    rows.append({
        "Fund": FUND_NAMES[fund], "New": r["n_added"], "Exited": r["n_exited"],
        "Resized": r["n_resized"],
        "Net Par Δ ($M)": round(r["net_par_change"] / 1e6, 1),
        "Positions": r["curr_positions"],
    })
if rows:
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    st.caption("Position-level detail is on the **Position Changes** page.")

st.divider()
st.caption(
    "Every figure is computed from SEC EDGAR NPORT-P filings — no estimates or third-party data. "
    "Marks are each fund's own reported fair value, struck on its own filing date, so cross-fund "
    "comparisons carry a timing difference of up to a quarter. Non-par lines (participation fees, "
    "common units) are excluded from price math."
)
