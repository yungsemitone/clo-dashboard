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

from src.auth import check_password
if not check_password():
    st.stop()
st.markdown("""<style>
    .block-container { padding-top: 1.5rem; }
    [data-testid="stSidebarNav"] li:has(a[href*="Filing_Detail"]) { display: none; }
</style>""", unsafe_allow_html=True)


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

# --- Source Filings ---
st.divider()
st.subheader("📄 Source Filing")

cik = fund["cik"]
edgar_nport_url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=NPORT-P&dateb=&owner=include&count=10"

filing_date_str = filing_date.strftime("%B %d, %Y") if filing_date else "Unknown"
filing_quarter = filing_date.strftime("%B %Y") if filing_date else ""

# Styled filing card
st.markdown(f"""
<div style="background: white; border: 1px solid #E0E0E0; border-radius: 10px;
            padding: 1.5rem; margin: 1rem 0;">
    <div style="display: flex; justify-content: space-between; align-items: center;">
        <div>
            <div style="font-size: 1.1rem; font-weight: 600; color: #1B4D3E;">
                NPORT-P — {filing_quarter}
            </div>
            <div style="font-size: 0.85rem; color: #888; margin-top: 4px;">
                Filed {filing_date_str} · {n_positions} holdings · SEC EDGAR
            </div>
        </div>
        <div style="background: #E8F5E9; color: #1B4D3E; padding: 4px 12px;
                    border-radius: 20px; font-size: 0.8rem; font-weight: 600;">
            Latest
        </div>
    </div>
</div>
""", unsafe_allow_html=True)

# Toggle summary view
summary_key = f"show_summary_{fund_ticker}"
if summary_key not in st.session_state:
    st.session_state[summary_key] = False

if st.button("📋 View Filing Summary", use_container_width=True, key=f"btn_{fund_ticker}"):
    st.session_state[summary_key] = not st.session_state[summary_key]

if st.session_state[summary_key]:
    st.markdown("---")

    # --- Generate summary paragraph ---
    top_mgr = df.groupby("manager")["par_amount"].sum().nlargest(3)
    top_mgr_names = ", ".join(top_mgr.index[:2]) + f", and {top_mgr.index[2]}" if len(top_mgr) >= 3 else " and ".join(top_mgr.index)

    priced_df = df.dropna(subset=["implied_price"])
    above_50 = len(priced_df[priced_df["implied_price"] >= 50])
    below_20 = len(priced_df[priced_df["implied_price"] < 20])

    # Largest position
    largest = df.iloc[0]
    largest_name = largest["deal_name"]
    largest_par = largest["par_amount"] / 1e6

    summary = (
        f"{fund['name']} reported {n_positions} CLO positions across {n_managers} managers "
        f"in its NPORT-P filing dated {filing_date_str}. The portfolio has a total par value of "
        f"${total_par / 1e6:,.1f}M with an aggregate market value of ${total_mv / 1e6:,.1f}M, "
        f"implying a weighted average price of {avg_price:.1f} cents per dollar of par. "
        f"The largest manager exposures are {top_mgr_names}, which together account for "
        f"${top_mgr.sum() / 1e6:,.0f}M in par. "
        f"The fund's largest single position is {largest_name} at ${largest_par:,.1f}M par. "
    )

    if above_50 > 0 or below_20 > 0:
        summary += (
            f"Across priced positions, {above_50} are marked above 50 cents "
            f"and {below_20} are marked below 20 cents, "
        )
        if below_20 > above_50:
            summary += "indicating the portfolio skews toward deeply discounted equity tranches."
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

    # --- Key stats (just a few, since detail is elsewhere) ---
    st.markdown("**Key Metrics**")
    kcol1, kcol2, kcol3, kcol4 = st.columns(4)
    kcol1.metric("Positions", n_positions)
    kcol2.metric("Avg Price", f"{avg_price:.1f}¢")

    # Concentration: top 5 managers % of portfolio
    top5_par = df.groupby("manager")["par_amount"].sum().nlargest(5).sum()
    top5_pct = (top5_par / total_par * 100) if total_par > 0 else 0
    kcol3.metric("Top 5 Mgr Concentration", f"{top5_pct:.0f}%")

    # Median price
    median_price = priced_df["implied_price"].median() if not priced_df.empty else 0
    kcol4.metric("Median Price", f"{median_price:.1f}¢")

    st.markdown("")

    # --- EDGAR button ---
    st.markdown(f"""
    <a href="{edgar_nport_url}" target="_blank" style="text-decoration: none;">
        <div style="background: #1B4D3E; color: white; padding: 12px 24px;
                    border-radius: 8px; text-align: center; font-weight: 600;
                    font-size: 0.95rem; margin-top: 8px; cursor: pointer;">
            View on SEC EDGAR →
        </div>
    </a>
    """, unsafe_allow_html=True)

    st.markdown("")

# --- Historical Filings ---
all_filing_dates = sorted(
    set(h.filing_date for h, _ in holdings if h.filing_date),
    reverse=True,
)

# Remove the latest (already shown above)
historical_dates = [d for d in all_filing_dates if d != filing_date]

if historical_dates:
    st.divider()
    st.subheader("📁 Historical Filings")
    st.caption("Click to view data from a previous filing period")

    for hist_date in historical_dates:
        hist_date_str = hist_date.strftime("%B %d, %Y")
        hist_quarter = hist_date.strftime("%B %Y")

        # Count positions for this filing date
        hist_count = sum(1 for h, _ in holdings if h.filing_date == hist_date)

        # Quick stats for this period
        hist_rows = [(h, d) for h, d in holdings if h.filing_date == hist_date]
        hist_par = sum(h.par_amount or 0 for h, _ in hist_rows)
        hist_mv = sum(h.market_value or 0 for h, _ in hist_rows)
        hist_price = (hist_mv / hist_par * 100) if hist_par > 0 else 0

        st.markdown(f"""
        <div style="background: white; border: 1px solid #E0E0E0; border-radius: 10px;
                    padding: 1.2rem; margin: 0.5rem 0;">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <div>
                    <div style="font-size: 1rem; font-weight: 600; color: #333;">
                        NPORT-P — {hist_quarter}
                    </div>
                    <div style="font-size: 0.8rem; color: #888; margin-top: 3px;">
                        Filed {hist_date_str} · {hist_count} holdings ·
                        Par ${hist_par / 1e6:,.0f}M · Avg Price {hist_price:.1f}¢
                    </div>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        if st.button(f"View {hist_quarter} Filing", key=f"hist_{fund_ticker}_{hist_date}", use_container_width=True):
            st.session_state["detail_fund"] = fund_ticker
            st.session_state["detail_date"] = str(hist_date)
            st.switch_page("pages/5_Filing_Detail.py")

st.divider()
st.caption("All data sourced from SEC EDGAR NPORT-P filings.")
