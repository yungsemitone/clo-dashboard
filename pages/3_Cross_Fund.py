import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from src.db import init_db, get_session
from src.models.schema import Deal, FundHolding

import yaml
from pathlib import Path

st.set_page_config(page_title="Cross-Fund Comparison", page_icon="🔀", layout="wide")
st.markdown("""<style>
    .block-container { padding-top: 1.5rem; }
    [data-testid="stSidebarNav"] li:has(a[href*="Filing_Detail"]) { display: none; }
    .stDeployButton { display: none !important; }
    [data-testid="stDecoration"] { display: none !important; }
</style>""", unsafe_allow_html=True)

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

st.title("🔀 Cross-Fund Comparison")
st.caption("Deals held by multiple funds — where do their valuations disagree?")

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
            "mv": h.market_value or 0, "price": price,
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
).reset_index()

# Only keep deals where 2+ funds have a real price
deal_spreads = deal_spreads[deal_spreads["funds"] >= 2]
deal_spreads["spread"] = deal_spreads["max_price"] - deal_spreads["min_price"]
deal_spreads = deal_spreads.sort_values("spread", ascending=False)

# Top metrics
n_shared = len(deal_spreads)
avg_spread = deal_spreads["spread"].mean()
max_spread_row = deal_spreads.iloc[0] if not deal_spreads.empty else None

col1, col2, col3 = st.columns(3)
col1.metric("Deals Held by 2+ Funds", n_shared)
col2.metric("Avg Price Spread", f"{avg_spread:.1f}¢")
if max_spread_row is not None:
    col3.metric("Widest Spread", f"{max_spread_row['spread']:.1f}¢")

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
                marker=dict(size=12, color={
                    "OXLC": "#1B4D3E", "ECC": "#2E7D5B", "OCCI": "#D4A843",
                    "PDCC": "#4A90D9", "PRIF": "#DC3545",
                }.get(h["fund"], "#888")),
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
    for fund, color in [("OXLC", "#1B4D3E"), ("ECC", "#2E7D5B"), ("OCCI", "#D4A843"),
                         ("PDCC", "#4A90D9"), ("PRIF", "#DC3545")]:
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

display = deal_spreads[["deal_name", "manager", "funds", "min_price", "max_price", "spread", "avg_price", "total_par"]].copy()
display["total_par"] = (display["total_par"] / 1e6).round(1)
display["min_price"] = display["min_price"].round(1)
display["max_price"] = display["max_price"].round(1)
display["spread"] = display["spread"].round(1)
display["avg_price"] = display["avg_price"].round(1)

display = display.rename(columns={
    "deal_name": "Deal", "manager": "Manager", "funds": "Funds",
    "min_price": "Low (¢)", "max_price": "High (¢)", "spread": "Spread (¢)",
    "avg_price": "Avg (¢)", "total_par": "Total Par ($M)",
})

st.dataframe(display, use_container_width=True, hide_index=True, height=400)

# Expandable per-deal detail
st.divider()
st.subheader("Deal Detail")

deal_options = dict(sorted({row["deal_name"]: row["deal_id"] for _, row in deal_spreads.iterrows()}.items()))
deal_col, _ = st.columns([2, 3])
with deal_col:
    selected_deal = st.selectbox("Select a cross-held deal", list(deal_options.keys()))
selected_id = deal_options[selected_deal]

deal_holdings = multi_df[multi_df["deal_id"] == selected_id].sort_values("fund")

if not deal_holdings.empty:
    detail_rows = []
    for _, h in deal_holdings.iterrows():
        detail_rows.append({
            "Fund": FUND_NAMES.get(h["fund"], h["fund"]),
            "Par": f"${h['par']:,.0f}",
            "Market Value": f"${h['mv']:,.0f}",
            "Implied Price": f"{h['price']:.1f}¢" if h["price"] else "N/A",
        })

    st.dataframe(pd.DataFrame(detail_rows), use_container_width=True, hide_index=True)

    # Bar chart comparing prices
    priced = deal_holdings.dropna(subset=["price"])
    if len(priced) >= 2:
        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=[FUND_NAMES.get(f, f) for f in priced["fund"]],
            y=priced["price"],
            marker_color=["#1B4D3E", "#2E7D5B", "#D4A843", "#4A90D9", "#DC3545"][:len(priced)],
            text=priced["price"].round(1),
            textposition="auto",
        ))
        fig.update_layout(
            yaxis_title="Implied Price (¢)", height=300,
            margin=dict(l=20, r=20, t=20, b=20),
        )
        st.plotly_chart(fig, use_container_width=True)

st.divider()
st.caption("All data sourced from SEC EDGAR NPORT-P filings.")
