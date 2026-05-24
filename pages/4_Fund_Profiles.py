"""
Fund Profiles — deep dive into each CLO equity fund's portfolio.
Shows positions, stats, manager concentration, pricing, and links to source filings.
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from src.db import init_db, get_session
from src.models.schema import Deal, FundHolding

import yaml
from pathlib import Path

st.set_page_config(page_title="Fund Profiles", page_icon="🏛️", layout="wide")
st.markdown("<style>.block-container { padding-top: 1.5rem; }</style>", unsafe_allow_html=True)


@st.cache_resource
def get_db():
    config_path = Path(__file__).parent.parent / "config.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)
    init_db(config)
    return get_session(config)


FUND_INFO = {
    "OXLC": {
        "name": "Oxford Lane Capital Corp.",
        "ticker": "OXLC",
        "cik": "0001495222",
        "description": "Oxford Lane Capital is a publicly traded closed-end fund focused on investing in CLO equity and debt tranches. Based in Greenwich, CT, it is the largest public vehicle dedicated to CLO equity, with a portfolio spanning hundreds of CLO deals across dozens of managers. The fund is externally managed by Oxford Lane Management LLC.",
        "exchange": "NASDAQ",
        "focus": "CLO equity and junior debt tranches",
    },
    "ECC": {
        "name": "Eagle Point Credit Company Inc.",
        "ticker": "ECC",
        "cik": "0001604174",
        "description": "Eagle Point Credit is a closed-end fund that primarily invests in equity and junior debt tranches of CLOs. Based in Greenwich, CT, Eagle Point also manages its own proprietary CLO platform, issuing CLOs under various 'Park' names (Basswood Park, Bear Mountain Park, Belmont Park, etc.). Externally managed by Eagle Point Credit Management LLC.",
        "exchange": "NYSE",
        "focus": "CLO equity, junior debt, and proprietary CLO issuance",
    },
    "OCCI": {
        "name": "OFS Credit Company, Inc.",
        "ticker": "OCCI",
        "cik": "0001716951",
        "description": "OFS Credit Company is a closed-end fund that invests primarily in CLO equity and debt securities. Managed by OFS Capital Management, the fund targets current income through exposure to the CLO equity tranche. It also holds positions in CLO mezzanine and senior debt tranches.",
        "exchange": "NASDAQ",
        "focus": "CLO equity and mezzanine tranches",
    },
    "PDCC": {
        "name": "Pearl Diver Credit Company, Inc.",
        "ticker": "PDCC",
        "cik": "0001998043",
        "description": "Pearl Diver Credit is a newer CLO-focused closed-end fund that invests in CLO equity and debt securities. The fund targets income generation through CLO equity distributions and capital appreciation through secondary market purchases of CLO tranches.",
        "exchange": "NYSE",
        "focus": "CLO equity and debt securities",
    },
    "PRIF": {
        "name": "Priority Income Fund, Inc.",
        "ticker": "PRIF",
        "cik": "0001554625",
        "description": "Priority Income Fund is a closed-end fund that invests primarily in CLO equity and mezzanine tranches. The fund is externally managed by Priority Senior Secured Income Management LLC, an affiliate of Prospect Capital Management. It has a diversified CLO portfolio across multiple managers and vintages.",
        "exchange": "NYSE",
        "focus": "CLO equity and mezzanine tranches",
    },
}

session = get_db()

st.title("🏛️ Fund Profiles")
st.caption("Deep dive into each CLO equity fund's portfolio")

# Fund selector
fund_options = {f"{info['name']} ({info['ticker']})": ticker for ticker, info in FUND_INFO.items()}
selected_label = st.selectbox("Select a fund", list(fund_options.keys()))
fund_ticker = fund_options[selected_label]
fund = FUND_INFO[fund_ticker]

st.divider()

# --- Fund header ---
st.subheader(fund["name"])
st.caption(f"{fund['exchange']}: {fund['ticker']}  ·  {fund['focus']}")
st.markdown(fund["description"])

st.divider()

# --- Load holdings for this fund ---
holdings = (
    session.query(FundHolding, Deal)
    .join(Deal, FundHolding.deal_id == Deal.id)
    .filter(FundHolding.source_fund == fund_ticker)
    .order_by(FundHolding.par_amount.desc())
    .all()
)

if not holdings:
    st.info(f"No holdings data for {fund['name']}.")
    st.stop()

# Build DataFrame
rows = []
for h, d in holdings:
    price = (h.market_value / h.par_amount * 100) if h.par_amount and h.par_amount > 0 and h.market_value else None
    rows.append({
        "deal_name": d.deal_name,
        "manager": d.manager,
        "par_amount": h.par_amount or 0,
        "market_value": h.market_value or 0,
        "implied_price": price,
        "cusip": h.cusip or "",
        "filing_date": h.filing_date,
    })
df = pd.DataFrame(rows)

# --- Portfolio metrics ---
total_par = df["par_amount"].sum()
total_mv = df["market_value"].sum()
avg_price = (total_mv / total_par * 100) if total_par > 0 else 0
n_positions = len(df)
n_managers = df["manager"].nunique()
filing_date = df["filing_date"].iloc[0] if not df.empty else None

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Positions", n_positions)
col2.metric("Managers", n_managers)
col3.metric("Total Par", f"${total_par / 1e6:,.1f}M")
col4.metric("Total Market Value", f"${total_mv / 1e6:,.1f}M")
col5.metric("Avg Implied Price", f"{avg_price:.1f}¢")

st.divider()

# --- Charts ---
chart1, chart2 = st.columns(2)

with chart1:
    st.subheader("Par by Manager (Top 15)")
    mgr_par = df.groupby("manager")["par_amount"].sum().nlargest(15).sort_values()
    mgr_par_mm = mgr_par / 1e6
    fig = px.bar(x=mgr_par_mm.values, y=mgr_par_mm.index, orientation="h",
                 color_discrete_sequence=["#1B4D3E"],
                 labels={"x": "Par ($M)", "y": ""})
    fig.update_layout(height=400, margin=dict(l=20, r=20, t=20, b=20))
    st.plotly_chart(fig, use_container_width=True)

with chart2:
    st.subheader("Price Distribution")
    price_data = df.dropna(subset=["implied_price"])
    price_data = price_data[price_data["implied_price"].between(0, 150)]
    if not price_data.empty:
        fig = px.histogram(price_data, x="implied_price", nbins=25,
                          color_discrete_sequence=["#1B4D3E"],
                          labels={"implied_price": "Implied Price (¢)"})
        fig.update_layout(height=400, margin=dict(l=20, r=20, t=20, b=20),
                         yaxis_title="Positions", showlegend=False)
        st.plotly_chart(fig, use_container_width=True)

# --- Manager concentration ---
st.divider()
st.subheader("Manager Concentration")

mgr_stats = df.groupby("manager").agg(
    positions=("deal_name", "count"),
    total_par=("par_amount", "sum"),
    total_mv=("market_value", "sum"),
).reset_index()
mgr_stats["avg_price"] = (mgr_stats["total_mv"] / mgr_stats["total_par"] * 100).where(mgr_stats["total_par"] > 0).round(1)
mgr_stats["par_pct"] = (mgr_stats["total_par"] / total_par * 100).round(1)
mgr_stats["par_mm"] = (mgr_stats["total_par"] / 1e6).round(2)
mgr_stats["mv_mm"] = (mgr_stats["total_mv"] / 1e6).round(2)

display_mgr = mgr_stats[["manager", "positions", "par_mm", "mv_mm", "avg_price", "par_pct"]].rename(columns={
    "manager": "Manager", "positions": "Positions", "par_mm": "Par ($M)",
    "mv_mm": "MV ($M)", "avg_price": "Avg Price (¢)", "par_pct": "% of Portfolio",
}).sort_values("Par ($M)", ascending=False)

st.dataframe(display_mgr, use_container_width=True, hide_index=True, height=350)

# Treemap of manager concentration
fig = px.treemap(
    mgr_stats.nlargest(20, "total_par"),
    path=["manager"], values="total_par",
    color="avg_price",
    color_continuous_scale=["#DC3545", "#FFC107", "#1B4D3E"],
    labels={"total_par": "Par Amount", "avg_price": "Avg Price (¢)", "manager": "Manager"},
)
fig.update_layout(height=400, margin=dict(l=10, r=10, t=10, b=10))
st.plotly_chart(fig, use_container_width=True)
st.caption("Size = par amount, color = avg implied price (green = higher)")

# --- Top and bottom positions ---
st.divider()

top_col, bot_col = st.columns(2)

with top_col:
    st.subheader("Largest Positions")
    top = df.nlargest(10, "par_amount")[["deal_name", "manager", "par_amount", "implied_price"]].copy()
    top["par_amount"] = (top["par_amount"] / 1e6).round(2)
    top["implied_price"] = top["implied_price"].round(1)
    top = top.rename(columns={
        "deal_name": "Deal", "manager": "Manager",
        "par_amount": "Par ($M)", "implied_price": "Price (¢)",
    })
    st.dataframe(top, use_container_width=True, hide_index=True)

with bot_col:
    st.subheader("Deepest Discounts")
    priced = df.dropna(subset=["implied_price"])
    priced = priced[priced["implied_price"] > 0]
    bottom = priced.nsmallest(10, "implied_price")[["deal_name", "manager", "par_amount", "implied_price"]].copy()
    bottom["par_amount"] = (bottom["par_amount"] / 1e6).round(2)
    bottom["implied_price"] = bottom["implied_price"].round(1)
    bottom = bottom.rename(columns={
        "deal_name": "Deal", "manager": "Manager",
        "par_amount": "Par ($M)", "implied_price": "Price (¢)",
    })
    st.dataframe(bottom, use_container_width=True, hide_index=True)

# --- Full holdings table ---
st.divider()
st.subheader("All Holdings")

full_display = df[["deal_name", "manager", "par_amount", "market_value", "implied_price", "cusip"]].copy()
full_display["par_amount"] = (full_display["par_amount"] / 1e6).round(2)
full_display["market_value"] = (full_display["market_value"] / 1e6).round(2)
full_display["implied_price"] = full_display["implied_price"].round(1)
full_display = full_display.rename(columns={
    "deal_name": "Deal", "manager": "Manager", "par_amount": "Par ($M)",
    "market_value": "MV ($M)", "implied_price": "Price (¢)", "cusip": "CUSIP",
})

st.dataframe(full_display, use_container_width=True, hide_index=True, height=500)

# --- Source filing links ---
st.divider()
st.subheader("📄 Source Filings")
st.caption("Links to the actual NPORT-P filings on SEC EDGAR")

cik = fund["cik"]
cik_clean = cik.lstrip("0")

# EDGAR company page
edgar_company = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=NPORT-P&dateb=&owner=include&count=10"
edgar_filings = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=NPORT-P&dateb=&owner=include&count=40&search_text=&action=getcompany"

if filing_date:
    st.markdown(f"**Latest filing used:** {filing_date.strftime('%B %d, %Y')}")

st.markdown(f"[View all {fund['ticker']} NPORT-P filings on EDGAR]({edgar_company})")
st.markdown(f"[{fund['ticker']} EDGAR company page](https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=&dateb=&owner=include&count=40)")
st.markdown(f"[{fund['ticker']} on SEC EDGAR (JSON API)](https://data.sec.gov/submissions/CIK{cik}.json)")

st.divider()
st.caption("All data sourced from SEC EDGAR NPORT-P filings.")
