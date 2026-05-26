import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import streamlit as st
import pandas as pd
import plotly.express as px

from src.db import init_db, get_session
from src.models.schema import Deal, FundHolding

import yaml
from pathlib import Path

st.set_page_config(page_title="Manager Rankings", page_icon="🏆", layout="wide")

from src.auth import check_password
if not check_password():
    st.stop()
st.markdown("""<style>
    .block-container { padding-top: 1.5rem; }
    [data-testid="stSidebarNav"] li:has(a[href*="Filing_Detail"]) { display: none; }
    .stDeployButton { display: none !important; }
    [data-testid="stDecoration"] { display: none !important; }
</style>""", unsafe_allow_html=True)


@st.cache_resource
def get_db():
    config_path = Path(__file__).parent.parent / "config.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)
    init_db(config)
    return get_session(config)


session = get_db()
st.title("🏆 Manager Rankings")

deals = session.query(Deal).all()
holdings = session.query(FundHolding).all()

if not deals:
    st.warning("No data available.")
    st.stop()

# Build manager stats from real data
deals_df = pd.DataFrame([{"deal_name": d.deal_name, "manager": d.manager, "deal_id": d.id} for d in deals])
holdings_df = pd.DataFrame([{
    "deal_id": h.deal_id, "par_amount": h.par_amount,
    "market_value": h.market_value, "source_fund": h.source_fund,
} for h in holdings])

merged = holdings_df.merge(deals_df, on="deal_id", how="left")

mgr_stats = merged.groupby("manager").agg(
    deals=("deal_name", "nunique"),
    total_par=("par_amount", "sum"),
    total_mv=("market_value", "sum"),
    positions=("deal_id", "count"),
    funds=("source_fund", "nunique"),
).reset_index()

mgr_stats["avg_price"] = (mgr_stats["total_mv"] / mgr_stats["total_par"] * 100).where(mgr_stats["total_par"] > 0).round(1)
mgr_stats["total_par_mm"] = (mgr_stats["total_par"] / 1e6).round(1)

# Top metrics
col1, col2, col3 = st.columns(3)
col1.metric("Total Managers", len(mgr_stats))
col2.metric("Total Deals", mgr_stats["deals"].sum())
col3.metric("Total Par Tracked", f"${mgr_stats['total_par'].sum() / 1e9:.1f}B")

st.divider()

# Leaderboard
st.subheader("Manager Leaderboard")
rank_col, _ = st.columns([2, 3])
with rank_col:
    sort_by = st.selectbox("Rank by", ["deals", "total_par_mm", "avg_price", "funds"])
ascending = sort_by == "avg_price"

ranked = mgr_stats.sort_values(sort_by, ascending=ascending, na_position="last").reset_index(drop=True)
ranked.index = ranked.index + 1
ranked.index.name = "Rank"

display = ranked[["manager", "deals", "total_par_mm", "avg_price", "positions", "funds"]].rename(columns={
    "manager": "Manager", "deals": "Deals", "total_par_mm": "Total Par ($M)",
    "avg_price": "Avg Price (¢)", "positions": "Positions", "funds": "Funds Holding",
})
st.dataframe(display, use_container_width=True, height=450)

st.divider()

# Charts
chart1, chart2 = st.columns(2)

with chart1:
    st.subheader("Par by Manager (Top 15)")
    top = mgr_stats.nlargest(15, "total_par_mm").sort_values("total_par_mm")
    fig = px.bar(top, x="total_par_mm", y="manager", orientation="h",
                 color_discrete_sequence=["#1B4D3E"],
                 labels={"total_par_mm": "Total Par ($M)", "manager": ""})
    fig.update_layout(height=400, margin=dict(l=20, r=20, t=20, b=20))
    st.plotly_chart(fig, use_container_width=True)

with chart2:
    st.subheader("Avg Implied Price by Manager (Top 15)")
    top_price = mgr_stats.dropna(subset=["avg_price"]).nlargest(15, "deals").sort_values("avg_price")
    if not top_price.empty:
        fig = px.bar(top_price, x="avg_price", y="manager", orientation="h",
                     color="avg_price",
                     color_continuous_scale=["#DC3545", "#FFC107", "#1B4D3E"],
                     labels={"avg_price": "Avg Price (¢ per $1 par)", "manager": ""})
        fig.update_layout(height=400, margin=dict(l=20, r=20, t=20, b=20), coloraxis_showscale=False)
        st.plotly_chart(fig, use_container_width=True)

st.divider()
st.caption("All data sourced from SEC EDGAR NPORT-P filings.")
