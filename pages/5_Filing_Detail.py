import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
"""
Filing Detail — view stats, summary, and holdings for a specific NPORT-P filing.
Accessed from the Fund Profiles page when clicking a historical filing.
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime

from src.db import init_db, get_session
from src.models.schema import Deal, FundHolding

import yaml
from pathlib import Path

st.set_page_config(page_title="Filing Detail", page_icon="📄", layout="wide")

from src.auth import check_password
if not check_password():
    st.stop()
st.markdown("""<style>
    .block-container { padding-top: 1.5rem; }
    [data-testid="stSidebarNav"] li:has(a[href*="Filing_Detail"]) { display: none; }
    [data-testid="stToolbar"] { display: none !important; }
    .stDeployButton { display: none !important; }
    [data-testid="stDecoration"] { display: none !important; }
    header[data-testid="stHeader"] { visibility: hidden; height: 0; }
</style>""", unsafe_allow_html=True)


@st.cache_resource
def get_db():
    config_path = Path(__file__).parent.parent / "config.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)
    init_db(config)
    return get_session(config)


FUND_INFO = {
    "OXLC": {"name": "Oxford Lane Capital Corp.", "cik": "0001495222"},
    "ECC": {"name": "Eagle Point Credit Company Inc.", "cik": "0001604174"},
    "OCCI": {"name": "OFS Credit Company, Inc.", "cik": "0001716951"},
    "PDCC": {"name": "Pearl Diver Credit Company, Inc.", "cik": "0001998043"},
    "PRIF": {"name": "Priority Income Fund, Inc.", "cik": "0001554625"},
}

session = get_db()

# --- Read from session state ---
fund_ticker = st.session_state.get("detail_fund")
detail_date_str = st.session_state.get("detail_date")

if not fund_ticker or not detail_date_str:
    st.warning("No filing selected. Go to Fund Profiles and click a historical filing.")
    st.stop()

fund = FUND_INFO.get(fund_ticker, {})
detail_date = datetime.strptime(detail_date_str, "%Y-%m-%d").date()

# --- Load holdings for this fund + date ---
holdings = (
    session.query(FundHolding, Deal)
    .join(Deal, FundHolding.deal_id == Deal.id)
    .filter(FundHolding.source_fund == fund_ticker)
    .filter(FundHolding.filing_date == detail_date)
    .order_by(FundHolding.par_amount.desc())
    .all()
)

if not holdings:
    st.warning(f"No data found for {fund_ticker} filing on {detail_date_str}.")
    st.stop()

# Build DataFrame
rows = []
for h, d in holdings:
    price = (h.market_value / h.par_amount * 100) if h.par_amount and h.par_amount > 0 and h.market_value else None
    rows.append({
        "deal_name": d.deal_name, "manager": d.manager,
        "par_amount": h.par_amount or 0, "market_value": h.market_value or 0,
        "implied_price": price, "cusip": h.cusip or "",
    })
df = pd.DataFrame(rows)

total_par = df["par_amount"].sum()
total_mv = df["market_value"].sum()
avg_price = (total_mv / total_par * 100) if total_par > 0 else 0
n_positions = len(df)
n_managers = df["manager"].nunique()

# --- Back button ---
if st.button("← Back to Fund Profile"):
    st.switch_page("pages/4_Fund_Profiles.py")

# --- Header ---
st.title(f"📄 {fund.get('name', fund_ticker)}")
st.caption(f"NPORT-P Filing — {detail_date.strftime('%B %d, %Y')}")

st.divider()

# --- Summary paragraph ---
top_mgr = df.groupby("manager")["par_amount"].sum().nlargest(3)
top_mgr_names = ", ".join(top_mgr.index[:2]) + f", and {top_mgr.index[2]}" if len(top_mgr) >= 3 else " and ".join(top_mgr.index)

priced_df = df.dropna(subset=["implied_price"])
above_50 = len(priced_df[priced_df["implied_price"] >= 50])
below_20 = len(priced_df[priced_df["implied_price"] < 20])

largest = df.iloc[0]
largest_name = largest["deal_name"]
largest_par = largest["par_amount"] / 1e6

summary = (
    f"{fund.get('name', fund_ticker)} reported {n_positions} CLO positions across {n_managers} managers "
    f"in its NPORT-P filing dated {detail_date.strftime('%B %d, %Y')}. The portfolio had a total par value of "
    f"${total_par / 1e6:,.1f}M with an aggregate market value of ${total_mv / 1e6:,.1f}M, "
    f"implying a weighted average price of {avg_price:.1f} cents per dollar of par. "
    f"The largest manager exposures were {top_mgr_names}, which together accounted for "
    f"${top_mgr.sum() / 1e6:,.0f}M in par. "
    f"The fund's largest single position was {largest_name} at ${largest_par:,.1f}M par. "
)

if above_50 > 0 or below_20 > 0:
    summary += (
        f"Across priced positions, {above_50} were marked above 50 cents "
        f"and {below_20} were marked below 20 cents, "
    )
    if below_20 > above_50:
        summary += "indicating the portfolio skewed toward deeply discounted equity tranches."
    elif above_50 > below_20:
        summary += "indicating a portfolio weighted toward performing equity."
    else:
        summary += "reflecting a mix of performing and distressed positions."

st.markdown(f"""
<div style="background: #F8FAF9; border-left: 4px solid #1B4D3E;
            padding: 1rem 1.2rem; border-radius: 0 6px 6px 0;
            line-height: 1.7; font-size: 0.95rem; color: #333;">
    {summary}
</div>
""", unsafe_allow_html=True)

st.markdown("")

# --- Key metrics ---
col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Positions", n_positions)
col2.metric("Managers", n_managers)
col3.metric("Total Par", f"${total_par / 1e6:,.1f}M")
col4.metric("Total MV", f"${total_mv / 1e6:,.1f}M")
col5.metric("Avg Price", f"{avg_price:.1f}¢")

st.divider()

# --- Charts ---
chart1, chart2 = st.columns(2)

with chart1:
    st.subheader("Par by Manager (Top 10)")
    mgr_par = df.groupby("manager")["par_amount"].sum().nlargest(10).sort_values() / 1e6
    fig = px.bar(x=mgr_par.values, y=mgr_par.index, orientation="h",
                 color_discrete_sequence=["#1B4D3E"],
                 labels={"x": "Par ($M)", "y": ""})
    fig.update_layout(height=350, margin=dict(l=20, r=20, t=20, b=20))
    st.plotly_chart(fig, use_container_width=True)

with chart2:
    st.subheader("Price Distribution")
    price_data = df.dropna(subset=["implied_price"])
    price_data = price_data[price_data["implied_price"].between(0, 150)]
    if not price_data.empty:
        fig = px.histogram(price_data, x="implied_price", nbins=20,
                          color_discrete_sequence=["#1B4D3E"],
                          labels={"implied_price": "Implied Price (¢)"})
        fig.update_layout(height=350, margin=dict(l=20, r=20, t=20, b=20),
                         yaxis_title="Positions", showlegend=False)
        st.plotly_chart(fig, use_container_width=True)

# --- Top and bottom positions ---
st.divider()
top_col, bot_col = st.columns(2)

with top_col:
    st.subheader("Largest Positions")
    top = df.nlargest(10, "par_amount")[["deal_name", "manager", "par_amount", "implied_price"]].copy()
    top["par_amount"] = (top["par_amount"] / 1e6).round(2)
    top["implied_price"] = top["implied_price"].round(1)
    top = top.rename(columns={"deal_name": "Deal", "manager": "Manager",
                               "par_amount": "Par ($M)", "implied_price": "Price (¢)"})
    st.dataframe(top, use_container_width=True, hide_index=True)

with bot_col:
    st.subheader("Deepest Discounts")
    priced = df.dropna(subset=["implied_price"])
    written_off = priced[priced["implied_price"] < 1]
    distressed = priced[priced["implied_price"] >= 1]
    if not distressed.empty:
        bottom = distressed.nsmallest(10, "implied_price")[["deal_name", "manager", "par_amount", "implied_price"]].copy()
        bottom["par_amount"] = (bottom["par_amount"] / 1e6).round(2)
        bottom["implied_price"] = bottom["implied_price"].round(1)
        bottom = bottom.rename(columns={"deal_name": "Deal", "manager": "Manager",
                                         "par_amount": "Par ($M)", "implied_price": "Price (¢)"})
        st.dataframe(bottom, use_container_width=True, hide_index=True)
    if len(written_off) > 0:
        wo_par = written_off["par_amount"].sum() / 1e6
        st.caption(f"{len(written_off)} positions (${wo_par:,.1f}M par) marked at near-zero value")

# --- Full holdings ---
st.divider()
st.subheader("All Holdings")

full = df[["deal_name", "manager", "par_amount", "market_value", "implied_price", "cusip"]].copy()
full["par_amount"] = (full["par_amount"] / 1e6).round(2)
full["market_value"] = (full["market_value"] / 1e6).round(2)
full["implied_price"] = full["implied_price"].round(1)
full = full.rename(columns={
    "deal_name": "Deal", "manager": "Manager", "par_amount": "Par ($M)",
    "market_value": "MV ($M)", "implied_price": "Price (¢)", "cusip": "CUSIP",
})
st.dataframe(full, use_container_width=True, hide_index=True, height=400)

# --- EDGAR link ---
st.divider()
cik = fund.get("cik", "")
edgar_url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=NPORT-P&dateb=&owner=include&count=10"

st.markdown(f"""
<a href="{edgar_url}" target="_blank" style="text-decoration: none;">
    <div style="background: #1B4D3E; color: white; padding: 12px 24px;
                border-radius: 8px; text-align: center; font-weight: 600;
                font-size: 0.95rem; cursor: pointer;">
        View All Filings on SEC EDGAR →
    </div>
</a>
""", unsafe_allow_html=True)

st.markdown("")
st.caption("All data sourced from SEC EDGAR NPORT-P filings.")
