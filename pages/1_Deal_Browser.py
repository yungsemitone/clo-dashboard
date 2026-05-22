"""Deal Browser — drill into individual CLO deals with real NPORT data."""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from src.db import init_db, get_session
from src.models.schema import Deal, FundHolding

import yaml
from pathlib import Path

st.set_page_config(page_title="Deal Browser", page_icon="🔍", layout="wide")
st.markdown("<style>.block-container { padding-top: 1.5rem; }</style>", unsafe_allow_html=True)


@st.cache_resource
def get_db():
    config_path = Path(__file__).parent.parent / "config.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)
    init_db(config)
    return get_session(config)


session = get_db()
st.title("🔍 Deal Browser")

deals = session.query(Deal).order_by(Deal.manager, Deal.deal_name).all()
if not deals:
    st.warning("No deals in the database yet.")
    st.stop()

deal_options = {f"{d.manager}  —  {d.deal_name}": d.id for d in deals}
selected_label = st.selectbox("Select a deal", list(deal_options.keys()))
deal_id = deal_options[selected_label]
deal = session.query(Deal).get(deal_id)

holdings = (
    session.query(FundHolding)
    .filter(FundHolding.deal_id == deal_id)
    .order_by(FundHolding.filing_date.desc())
    .all()
)

st.divider()

# Deal header
hcol1, hcol2, hcol3, hcol4 = st.columns(4)
hcol1.metric("Manager", deal.manager)
hcol2.metric("Deal Name", deal.deal_name)

if holdings:
    total_par = sum(h.par_amount or 0 for h in holdings)
    total_mv = sum(h.market_value or 0 for h in holdings)
    avg_price = (total_mv / total_par * 100) if total_par > 0 else 0
    hcol3.metric("Total Par Held", f"${total_par / 1e6:,.2f}M")
    hcol4.metric("Implied Price", f"{avg_price:.1f}¢" if avg_price > 0 else "N/A")

st.divider()

# Fund holdings table
if holdings:
    st.subheader("Fund Positions")
    st.caption("Which public CLO equity funds hold this deal (from EDGAR NPORT-P filings)")

    fund_names = {"OXLC": "Oxford Lane Capital", "ECC": "Eagle Point Credit",
                  "OCCI": "OFS Credit Company", "PDCC": "Pearl Diver Credit",
                  "PRIF": "Priority Income Fund"}

    rows = []
    for h in holdings:
        price = (h.market_value / h.par_amount * 100) if h.par_amount and h.par_amount > 0 else None
        rows.append({
            "Fund": fund_names.get(h.source_fund, h.source_fund),
            "Filing Date": h.filing_date,
            "Par Amount": f"${h.par_amount:,.0f}" if h.par_amount else "N/A",
            "Market Value": f"${h.market_value:,.0f}" if h.market_value else "N/A",
            "Implied Price": f"{price:.1f}¢" if price else "N/A",
            "CUSIP": h.cusip or "",
        })

    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # Price comparison across funds
    if len(holdings) > 1:
        st.divider()
        st.subheader("Price by Fund")

        price_data = []
        for h in holdings:
            if h.par_amount and h.par_amount > 0:
                price_data.append({
                    "fund": fund_names.get(h.source_fund, h.source_fund),
                    "price": h.market_value / h.par_amount * 100,
                    "par": h.par_amount / 1e6,
                })

        if price_data:
            pdf = pd.DataFrame(price_data)
            fig = go.Figure()
            fig.add_trace(go.Bar(
                x=pdf["fund"], y=pdf["price"],
                marker_color="#1B4D3E",
                text=pdf["price"].round(1),
                textposition="auto",
            ))
            fig.update_layout(
                yaxis_title="Implied Price (¢ per $1 par)",
                height=350, margin=dict(l=20, r=20, t=20, b=20),
            )
            st.plotly_chart(fig, use_container_width=True)
else:
    st.info("No holdings data for this deal.")

st.divider()
st.caption("All data sourced from SEC EDGAR NPORT-P filings filed by public CLO equity funds.")
