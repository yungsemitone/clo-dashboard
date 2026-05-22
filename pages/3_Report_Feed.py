"""Holdings Feed — latest NPORT-P position data from CLO equity funds."""

import streamlit as st
import pandas as pd

from src.db import init_db, get_session
from src.models.schema import Deal, FundHolding

import yaml
from pathlib import Path

st.set_page_config(page_title="Holdings Feed", page_icon="📰", layout="wide")

st.markdown("""<style>
    .block-container { padding-top: 1.5rem; }
    .holding-card {
        background: white; border: 1px solid #E0E0E0;
        border-radius: 8px; padding: 1.2rem; margin-bottom: 0.8rem;
    }
    .holding-deal { font-size: 1.05rem; font-weight: 600; color: #1B4D3E; }
    .holding-meta { font-size: 0.85rem; color: #666; margin: 0.3rem 0 0.6rem 0; }
    .holding-metrics { display: flex; gap: 2rem; flex-wrap: wrap; }
    .holding-metric { text-align: center; }
    .holding-metric-value { font-size: 1.15rem; font-weight: 600; }
    .holding-metric-label { font-size: 0.7rem; color: #888; }
    .price-good { color: #1B4D3E; }
    .price-mid { color: #D4A843; }
    .price-low { color: #DC3545; }
</style>""", unsafe_allow_html=True)


@st.cache_resource
def get_db():
    config_path = Path(__file__).parent.parent / "config.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)
    init_db(config)
    return get_session(config)


session = get_db()

st.title("📰 Holdings Feed")
st.caption("Latest CLO positions from public fund NPORT-P filings")

# Filters
filter1, filter2, filter3 = st.columns([2, 1, 1])

fund_names = {"All": "All", "OXLC": "Oxford Lane", "ECC": "Eagle Point",
              "OCCI": "OFS Credit", "PDCC": "Pearl Diver", "PRIF": "Priority Income"}

with filter1:
    managers = ["All"] + sorted(set(d.manager for d in session.query(Deal).all()))
    mgr_filter = st.selectbox("Manager", managers)

with filter2:
    fund_filter = st.selectbox("Fund", list(fund_names.keys()), format_func=lambda x: fund_names[x])

with filter3:
    limit = st.selectbox("Show", [25, 50, 100, 200], index=0)

# Query
query = (
    session.query(FundHolding, Deal)
    .join(Deal, FundHolding.deal_id == Deal.id)
    .order_by(FundHolding.par_amount.desc())
)

if mgr_filter != "All":
    query = query.filter(Deal.manager == mgr_filter)
if fund_filter != "All":
    query = query.filter(FundHolding.source_fund == fund_filter)

results = query.limit(limit).all()

if not results:
    st.info("No holdings found.")
    st.stop()

st.markdown(f"Showing **{len(results)}** positions")
st.divider()

# Render cards
for h, deal in results:
    price = (h.market_value / h.par_amount * 100) if h.par_amount and h.par_amount > 0 else None

    price_class = "price-good"
    if price is not None:
        if price < 30:
            price_class = "price-low"
        elif price < 60:
            price_class = "price-mid"

    par_str = f"${h.par_amount:,.0f}" if h.par_amount else "—"
    mv_str = f"${h.market_value:,.0f}" if h.market_value else "—"
    price_str = f"{price:.1f}¢" if price else "—"
    fund_label = fund_names.get(h.source_fund, h.source_fund)
    date_str = h.filing_date.strftime("%B %d, %Y") if h.filing_date else ""

    st.markdown(f"""
    <div class="holding-card">
        <div class="holding-deal">{deal.deal_name}</div>
        <div class="holding-meta">{deal.manager} · {fund_label} · Filed {date_str}</div>
        <div class="holding-metrics">
            <div class="holding-metric">
                <div class="holding-metric-value">{par_str}</div>
                <div class="holding-metric-label">Par Amount</div>
            </div>
            <div class="holding-metric">
                <div class="holding-metric-value">{mv_str}</div>
                <div class="holding-metric-label">Market Value</div>
            </div>
            <div class="holding-metric">
                <div class="holding-metric-value {price_class}">{price_str}</div>
                <div class="holding-metric-label">Implied Price</div>
            </div>
            <div class="holding-metric">
                <div class="holding-metric-value">{h.cusip or '—'}</div>
                <div class="holding-metric-label">CUSIP</div>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

st.divider()
st.caption("All data sourced from SEC EDGAR NPORT-P filings filed by public CLO equity funds.")
