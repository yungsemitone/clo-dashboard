import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import pandas as pd
import plotly.express as px

from src.db import init_db, get_session
from src.analytics.vintage import vintage_summary, unknown_vintage_count

import yaml
from pathlib import Path

st.set_page_config(page_title="Vintage Analysis", page_icon="📅", layout="wide")
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


session = get_db()

st.title("📅 Vintage Analysis")
st.caption("How CLO equity is priced by origination year — across the funds' latest filings")

vs = vintage_summary(session)
n_unknown = unknown_vintage_count(session)

if vs.empty:
    st.warning("No deals with a parseable vintage year.")
    st.stop()

n_known = int(vs["n_deals"].sum())
min_year, max_year = int(vs["vintage"].min()), int(vs["vintage"].max())

m1, m2, m3 = st.columns(3)
m1.metric("Deals with a Vintage", n_known)
m2.metric("Vintage Span", f"{min_year}–{max_year}")
m3.metric("No Parseable Year", n_unknown)

st.divider()

# --- Avg price by vintage ---
st.subheader("Average Implied Price by Vintage")
st.caption("Par-weighted, excluding near-zero write-offs (<1¢). Older vintages have amortized down; newer deals sit closer to par.")

priced = vs.dropna(subset=["avg_price"]).copy()
if not priced.empty:
    fig = px.bar(
        priced, x="vintage", y="avg_price",
        color="avg_price", color_continuous_scale=["#DC3545", "#FFC107", "#1B4D3E"],
        labels={"vintage": "Vintage Year", "avg_price": "Avg Implied Price (¢)"},
        range_color=[0, 100],
    )
    fig.update_layout(height=420, margin=dict(l=20, r=20, t=20, b=20), coloraxis_showscale=False)
    fig.update_xaxes(dtick=1)
    st.plotly_chart(fig, use_container_width=True)

# --- Par by vintage ---
st.subheader("Total Par by Vintage")
fig = px.bar(
    vs, x="vintage", y=vs["total_par"] / 1e6,
    color_discrete_sequence=["#1B4D3E"],
    labels={"vintage": "Vintage Year", "y": "Total Par ($M)"},
)
fig.update_layout(height=380, margin=dict(l=20, r=20, t=20, b=20))
fig.update_xaxes(dtick=1)
fig.update_yaxes(title="Total Par ($M)")
st.plotly_chart(fig, use_container_width=True)

st.divider()

# --- Table ---
st.subheader("By Vintage")
display = vs.copy()
display["total_par"] = (display["total_par"] / 1e6).round(1)
display["avg_price"] = display["avg_price"].round(1)
display["median_price"] = display["median_price"].round(1)
display = display[["vintage", "n_deals", "total_par", "avg_price", "median_price"]].rename(columns={
    "vintage": "Vintage", "n_deals": "Deals", "total_par": "Total Par ($M)",
    "avg_price": "Avg Price (¢)", "median_price": "Median Price (¢)",
}).sort_values("Vintage", ascending=False)
st.dataframe(display, use_container_width=True, hide_index=True, height=400)

st.caption(
    f"Vintage is parsed from the deal name. {n_unknown} deals use Roman-numeral or running "
    f"series naming (e.g. \"Madison Park Funding XXIV\", \"Venture XX CLO\") with no year, and "
    f"are excluded here rather than bucketed into a guessed vintage. All data from SEC EDGAR "
    f"NPORT-P filings, latest filing per fund."
)
