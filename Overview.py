import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime

from src.db import init_db, get_session
from src.models.schema import Deal, FundHolding
from src.ui import apply_chrome

st.set_page_config(page_title="CLO Dashboard", page_icon="📊", layout="wide",
                   initial_sidebar_state="expanded")

from src.auth import check_password
if not check_password():
    st.stop()

apply_chrome()


@st.cache_resource
def get_db():
    import yaml
    from pathlib import Path
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)
    init_db(config)
    return get_session(config)


session = get_db()

# Sidebar
with st.sidebar:
    st.title("🏦 CLO Monitor")
    st.caption("Real data from SEC EDGAR")
    st.divider()
    st.markdown("**Data Source**")
    st.markdown("NPORT-P filings from public CLO equity funds (OXLC, ECC, OCCI, PDCC, PRIF)")
    st.divider()
    if st.button("🔄 Refresh Data", use_container_width=True):
        st.cache_resource.clear()
        st.rerun()

# Load data
deals = session.query(Deal).all()
holdings = session.query(FundHolding).all()

if not deals:
    st.warning("No data yet. Run `python run_pipeline.py` to scrape EDGAR.")
    st.stop()

# Build DataFrames
deals_df = pd.DataFrame([{
    "id": d.id, "deal_name": d.deal_name, "manager": d.manager,
} for d in deals])

holdings_df = pd.DataFrame([{
    "deal_id": h.deal_id, "source_fund": h.source_fund,
    "filing_date": h.filing_date, "par_amount": h.par_amount,
    "market_value": h.market_value,
} for h in holdings])

if not holdings_df.empty:
    holdings_df["implied_price"] = (holdings_df["market_value"] / holdings_df["par_amount"] * 100).where(holdings_df["par_amount"] > 0)
    merged = holdings_df.merge(deals_df, left_on="deal_id", right_on="id", how="left")

# Header
st.title("CLO Deal Intelligence")
st.caption(f"Data scraped from SEC EDGAR NPORT-P filings · {datetime.now().strftime('%B %d, %Y')}")

# Top metrics
col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Deals Tracked", len(deals))
col2.metric("Unique Managers", deals_df["manager"].nunique())

if not holdings_df.empty:
    total_par = holdings_df["par_amount"].sum()
    total_mv = holdings_df["market_value"].sum()
    avg_price = (total_mv / total_par * 100) if total_par > 0 else 0
    n_funds = holdings_df["source_fund"].nunique()

    col3.metric("Total Par Held", f"${total_par / 1e9:.1f}B")
    col4.metric("Avg Implied Price", f"{avg_price:.1f}¢")
    col5.metric("Source Funds", n_funds)

st.divider()

# Charts
if not holdings_df.empty:
    chart1, chart2 = st.columns(2)

    with chart1:
        st.subheader("Deals by Manager (Top 15)")
        mgr_counts = deals_df["manager"].value_counts().head(15).sort_values()
        fig = px.bar(x=mgr_counts.values, y=mgr_counts.index, orientation="h",
                     color_discrete_sequence=["#CBA255"],
                     labels={"x": "Number of Deals", "y": ""})
        fig.update_layout(height=400, margin=dict(l=20, r=20, t=20, b=20))
        st.plotly_chart(fig, use_container_width=True)

    with chart2:
        st.subheader("Implied Price Distribution")
        price_data = merged.dropna(subset=["implied_price"])
        price_data = price_data[price_data["implied_price"].between(0, 150)]
        if not price_data.empty:
            fig = px.histogram(price_data, x="implied_price", nbins=30,
                              color_discrete_sequence=["#CBA255"],
                              labels={"implied_price": "Implied Price (¢ per $1 par)"})
            fig.update_layout(height=400, margin=dict(l=20, r=20, t=20, b=20),
                             yaxis_title="Number of Positions", showlegend=False,
                             bargap=0.04)
            fig.update_traces(marker_line_color="#1A1513", marker_line_width=1.5)
            st.plotly_chart(fig, use_container_width=True)

    st.divider()

    # Par by source fund
    st.subheader("Holdings by Source Fund")
    fund_stats = merged.groupby("source_fund").agg(
        positions=("deal_id", "count"),
        total_par=("par_amount", "sum"),
        total_mv=("market_value", "sum"),
    ).reset_index()
    fund_stats["avg_price"] = (fund_stats["total_mv"] / fund_stats["total_par"] * 100).round(1)
    fund_stats["total_par_mm"] = (fund_stats["total_par"] / 1e6).round(1)
    fund_stats["total_mv_mm"] = (fund_stats["total_mv"] / 1e6).round(1)

    fund_names = {"OXLC": "Oxford Lane", "ECC": "Eagle Point", "OCCI": "OFS Credit",
                  "PDCC": "Pearl Diver", "PRIF": "Priority Income"}
    fund_stats["Fund"] = fund_stats["source_fund"].map(fund_names).fillna(fund_stats["source_fund"])

    display = fund_stats[["Fund", "positions", "total_par_mm", "total_mv_mm", "avg_price"]].rename(columns={
        "positions": "Positions", "total_par_mm": "Par ($M)",
        "total_mv_mm": "Market Value ($M)", "avg_price": "Avg Price (¢)",
    })
    st.dataframe(display, use_container_width=True, hide_index=True)

    st.divider()

    # Deals table
    st.subheader("All Deals")
    deal_summary = merged.groupby(["deal_name", "manager"]).agg(
        par=("par_amount", "sum"),
        mv=("market_value", "sum"),
        funds=("source_fund", lambda x: ", ".join(sorted(x.unique()))),
    ).reset_index()
    deal_summary["price"] = (deal_summary["mv"] / deal_summary["par"] * 100).where(deal_summary["par"] > 0).round(1)
    deal_summary["par_mm"] = (deal_summary["par"] / 1e6).round(2)
    deal_summary["mv_mm"] = (deal_summary["mv"] / 1e6).round(2)

    display2 = deal_summary[["deal_name", "manager", "par_mm", "mv_mm", "price", "funds"]].rename(columns={
        "deal_name": "Deal", "manager": "Manager", "par_mm": "Par ($M)",
        "mv_mm": "MV ($M)", "price": "Price (¢)", "funds": "Held By",
    }).sort_values("Par ($M)", ascending=False)

    st.dataframe(display2, use_container_width=True, hide_index=True, height=500)
