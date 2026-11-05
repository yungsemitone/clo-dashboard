import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from src.db import init_db, get_session
from src.models.schema import Deal, FundHolding
from src.ui import apply_chrome, page_intro, FUND_COLORS
from src.analytics.valuation_notes import explain_cross_held, PRIMER, fund_book_posture, pairwise_bias
from src.analytics.vintage import parse_vintage
from src.summarizer import generate_valuation_explainer, ai_summaries_enabled

import yaml
from pathlib import Path

st.set_page_config(page_title="Cross-Fund Comparison", page_icon="🔀", layout="wide")
apply_chrome()

from src.auth import check_password
if not check_password():
    st.stop()


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

page_intro("Cross-Fund", "See where funds mark the same CLO differently, then check whether they own the identical security or different tranches.", "🔀")

st.caption("Five public CLO-equity funds · latest NPORT-P filing per fund · prices are cents per $1 of par; near-zero and missing marks are excluded from comparisons.")
with st.expander("How to read prices, tranches, and CUSIPs"):
    st.markdown(PRIMER)

# Find deals held by 2+ funds (latest filing per fund only)
holdings = session.query(FundHolding, Deal).join(Deal, FundHolding.deal_id == Deal.id).all()

if not holdings:
    st.warning("No data available.")
    st.stop()

# Get latest filing date per fund
from collections import defaultdict
fund_latest = {}
for h, d in holdings:
    key = h.source_fund
    if key not in fund_latest or h.filing_date > fund_latest[key]:
        fund_latest[key] = h.filing_date

# Filter to latest filings only
rows = []
for h, d in holdings:
    if h.filing_date == fund_latest.get(h.source_fund):
        price = (h.market_value / h.par_amount * 100) if h.par_amount and h.par_amount > 0 and h.market_value else None
        rows.append({
            "deal_id": d.id, "deal_name": d.deal_name, "manager": d.manager,
            "fund": h.source_fund, "par": h.par_amount or 0,
            "mv": h.market_value or 0, "price": price, "cusip": h.cusip or "",
        })

df = pd.DataFrame(rows)

# Find deals held by multiple funds
deal_fund_counts = df.groupby("deal_id")["fund"].nunique()
multi_fund_deals = deal_fund_counts[deal_fund_counts >= 2].index.tolist()

multi_df = df[df["deal_id"].isin(multi_fund_deals)].copy()

if multi_df.empty:
    st.info("No deals are currently held by multiple funds.")
    st.stop()

# Compute spread (max price - min price) per deal
# Exclude near-zero marks (write-offs) — not real valuation disagreements
priced_multi = multi_df.dropna(subset=["price"])
priced_multi = priced_multi[priced_multi["price"] >= 1]

deal_spreads = priced_multi.groupby(["deal_id", "deal_name", "manager"]).agg(
    funds=("fund", "nunique"),
    min_price=("price", "min"),
    max_price=("price", "max"),
    avg_price=("price", "mean"),
    total_par=("par", "sum"),
    cusips=("cusip", lambda x: len({c for c in x if c})),
).reset_index()

# Only keep deals where 2+ funds have a real price
deal_spreads = deal_spreads[deal_spreads["funds"] >= 2]
deal_spreads["spread"] = deal_spreads["max_price"] - deal_spreads["min_price"]
deal_spreads["Comparison"] = deal_spreads["cusips"].map(lambda n: "Same security" if n <= 1 else "Different tranches")
deal_spreads = deal_spreads.sort_values("spread", ascending=False)

# Top metrics
n_shared = len(deal_spreads)
avg_spread = deal_spreads["spread"].mean()
max_spread_row = deal_spreads.iloc[0] if not deal_spreads.empty else None

col1, col2, col3 = st.columns(3)
col1.metric("Comparable Shared Deals", n_shared)
col2.metric("Same CUSIP", int((deal_spreads["Comparison"] == "Same security").sum()))
col3.metric("Different Tranches", int((deal_spreads["Comparison"] == "Different tranches").sum()))
st.caption(f"{len(multi_fund_deals)} deals are held by 2+ funds; {n_shared} have usable marks of at least 1¢ at two or more funds and appear below. "
           f"Average gap: {avg_spread:.1f}¢. Matching CUSIPs indicate the identical security; different CUSIPs usually mean different tranches.")

st.divider()

# Chart: biggest valuation disagreements
st.subheader("Largest Valuation Disagreements")
st.caption("Deals where funds mark the same position at the most different prices")

top_spread = deal_spreads.head(15)

if not top_spread.empty:
    fig = go.Figure()

    for _, row in top_spread.iterrows():
        deal_holdings = multi_df[multi_df["deal_id"] == row["deal_id"]].dropna(subset=["price"])
        deal_holdings = deal_holdings[deal_holdings["price"] >= 1]
        for _, h in deal_holdings.iterrows():
            fig.add_trace(go.Scatter(
                x=[h["price"]],
                y=[row["deal_name"][:40]],
                mode="markers",
                marker=dict(size=12, color=FUND_COLORS.get(h["fund"], "#888")),
                name=FUND_NAMES.get(h["fund"], h["fund"]),
                showlegend=False,
                hovertemplate=f"{FUND_NAMES.get(h['fund'], h['fund'])}: {h['price']:.1f}¢<br>Par: ${h['par']/1e6:.1f}M<extra></extra>",
            ))

        # Draw spread line
        prices = deal_holdings["price"].tolist()
        if len(prices) >= 2:
            fig.add_trace(go.Scatter(
                x=[min(prices), max(prices)],
                y=[row["deal_name"][:40], row["deal_name"][:40]],
                mode="lines", line=dict(color="#CCC", width=2),
                showlegend=False, hoverinfo="skip",
            ))

    # Add legend manually
    for fund, color in FUND_COLORS.items():
        if fund in multi_df["fund"].values:
            fig.add_trace(go.Scatter(
                x=[None], y=[None], mode="markers",
                marker=dict(size=10, color=color),
                name=FUND_NAMES[fund],
            ))

    # Order: highest spread at top (Plotly y-axis goes bottom-to-top)
    deal_order = list(reversed([row["deal_name"][:40] for _, row in top_spread.iterrows()]))

    fig.update_layout(
        xaxis_title="Implied Price (¢)",
        yaxis=dict(categoryorder="array", categoryarray=deal_order),
        height=max(400, len(top_spread) * 35),
        margin=dict(l=20, r=20, t=20, b=40),
        legend=dict(orientation="h", y=-0.15),
    )
    st.plotly_chart(fig, use_container_width=True)

st.divider()

# Full table
st.subheader("All Cross-Held Deals")

display = deal_spreads[["deal_name", "Comparison", "min_price", "max_price", "spread", "total_par"]].copy()
display["total_par"] = (display["total_par"] / 1e6).round(1)
display["min_price"] = display["min_price"].round(1)
display["max_price"] = display["max_price"].round(1)
display["spread"] = display["spread"].round(1)
display = display.rename(columns={
    "deal_name": "Deal", "min_price": "Low ¢", "max_price": "High ¢",
    "spread": "Gap ¢", "total_par": "Combined Par $M",
})

st.dataframe(display, use_container_width=True, hide_index=True, height=400)

# Expandable per-deal detail
st.divider()
st.subheader("Deal Detail")

deal_options = {row["deal_name"]: row["deal_id"] for _, row in deal_spreads.iterrows()}
deal_col, _ = st.columns([2, 3])
with deal_col:
    selected_deal = st.selectbox("Deal (ranked by largest valuation gap)", list(deal_options.keys()))
selected_id = deal_options[selected_deal]

deal_holdings = multi_df[multi_df["deal_id"] == selected_id].sort_values("fund")

if not deal_holdings.empty:
    detail_rows = []
    for _, h in deal_holdings.iterrows():
        detail_rows.append({
            "Fund": FUND_NAMES.get(h["fund"], h["fund"]),
            "Par": f"${h['par']:,.0f}",
            "Market Value": f"${h['mv']:,.0f}",
            "Price ¢": f"{h['price']:.1f}" if pd.notna(h["price"]) else "—",
            "CUSIP": h["cusip"] or "—",
            "Filing Date": fund_latest.get(h["fund"]),
        })

    st.dataframe(pd.DataFrame(detail_rows), use_container_width=True, hide_index=True)

    # Bar chart comparing prices
    priced = deal_holdings.dropna(subset=["price"])
    if len(priced) >= 2:
        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=[FUND_NAMES.get(f, f) for f in priced["fund"]],
            y=priced["price"],
            marker_color=[FUND_COLORS.get(f, "#888") for f in priced["fund"]],
            text=priced["price"].round(1),
            textposition="auto",
        ))
        fig.update_layout(
            yaxis_title="Implied Price (¢)", height=300,
            margin=dict(l=20, r=20, t=20, b=20),
        )
        st.plotly_chart(fig, use_container_width=True)

    # --- Why these marks differ ---
    sel_manager = deal_holdings["manager"].iloc[0]
    records = deal_holdings.to_dict("records")
    fund_postures = fund_book_posture(df)
    deal_funds = list(deal_holdings["fund"])
    pair_bias = pairwise_bias(df, deal_funds[0], deal_funds[1]) if len(deal_funds) == 2 else None
    note = explain_cross_held(selected_deal, sel_manager, records,
                              fund_postures=fund_postures, pair_bias=pair_bias,
                              fund_names=FUND_NAMES)

    st.markdown("#### Three things to know")
    same_security = note["cusip_case"] == "same"
    comparison_text = ("Identical CUSIP—the price gap is a valuation difference." if same_security
                       else "Different CUSIPs—the funds likely own different tranches." if note["cusip_case"] == "different"
                       else "CUSIP detail is incomplete—tranche comparability is uncertain.")
    dates_used = ", ".join(f"{FUND_NAMES.get(r['fund'], r['fund'])} {fund_latest.get(r['fund']):%b %d, %Y}" for r in records)
    st.markdown(f"- **Security:** {comparison_text}\n"
                f"- **Price gap:** {note['summary']}\n"
                f"- **Timing:** {dates_used}. Marks are not necessarily struck on the same day.")
    with st.expander("Full valuation rationale and portfolio context"):
        for reason in note["reasons"]:
            st.markdown("- " + reason)
        st.caption("Portfolio-average marks in this rationale include priced (≥1¢) latest holdings; other pages may use all filings or exclude non-par instruments, so averages can differ by design.")

    if ai_summaries_enabled():
        if st.button("✨ Analyst note (AI)", key=f"vexp_{selected_id}"):
            ctx = {
                "deal_name": selected_deal,
                "manager": sel_manager,
                "vintage": parse_vintage(selected_deal),
                "cusip_case": note["cusip_case"],
                "marks": [
                    {"fund": r["fund"], "price": r["price"], "cusip": r.get("cusip")}
                    for r in records if r.get("price")
                ],
                "fallback": note["summary"] + " " + " ".join(note["reasons"]),
                "portfolio_context": note.get("portfolio_context", ""),
            }
            with st.spinner("Thinking…"):
                explainer = generate_valuation_explainer(ctx)
            st.markdown(
                f'<div style="background:#251D19;border-left:4px solid #CBA255;'
                f'padding:1rem 1.2rem;border-radius:0 6px 6px 0;line-height:1.7;color:#C9BCAF;">'
                f'{explainer}</div>', unsafe_allow_html=True)
            st.caption("✨ Generated by Claude, grounded in the marks above.")

st.divider()
st.caption("All data sourced from SEC EDGAR NPORT-P filings.")
