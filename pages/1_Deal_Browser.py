import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import re
import streamlit as st
import pandas as pd

from src.db import init_db, get_session
from src.models.schema import Deal, FundHolding

import yaml
from pathlib import Path
from collections import OrderedDict

st.set_page_config(page_title="Deal Browser", page_icon="🔍", layout="wide")

from src.auth import check_password
if not check_password():
    st.stop()
st.markdown("""<style>
    .block-container { padding-top: 1.5rem; }
    [data-testid="stSidebarNav"] li:has(a[href*="Filing_Detail"]) { display: none; }
    [data-testid="stToolbar"] { display: none !important; }
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


FUND_NAMES = {"OXLC": "Oxford Lane Capital", "ECC": "Eagle Point Credit",
              "OCCI": "OFS Credit Company", "PDCC": "Pearl Diver Credit",
              "PRIF": "Priority Income Fund"}


def get_base_name(deal_name: str) -> str:
    """Group deals by stripping vintage/series identifiers."""
    name = deal_name.strip()
    for suffix in ["Ltd", "Ltd.", "LLC", "LP", "Inc", "Inc.", "DAC", "L.P."]:
        if name.endswith(suffix):
            name = name[:-len(suffix)].strip().rstrip(",").strip()
    name = re.sub(r'\s+\d{4}[-/]?\d{0,3}[A-Za-z]*\s*$', '', name)
    name = re.sub(r'\s*[-]\d+[A-Za-z]*\s*$', '', name)
    for suffix in ["Ltd", "Ltd.", "LLC", "LP", "Inc", "Inc.", "DAC", "L.P."]:
        if name.endswith(suffix):
            name = name[:-len(suffix)].strip().rstrip(",").strip()
    name = re.sub(r'\s+[IVXLCDM]{1,8}(?=\s+CLO|\s+Clo|\s+Fund|\s*$)', '', name)
    name = re.sub(r'\s+\d{1,3}(?=\s+CLO|\s+Clo|\s+Fund|\s*$)', '', name)
    return name.strip() or deal_name


session = get_db()
st.title("🔍 Deal Browser")

deals = session.query(Deal).order_by(Deal.manager, Deal.deal_name).all()
if not deals:
    st.warning("No deals in the database yet.")
    st.stop()

# Step 1: Select a manager
managers = sorted(set(d.manager for d in deals))
selected_manager = st.selectbox("Select a manager", managers)
manager_deals = [d for d in deals if d.manager == selected_manager]

st.divider()

# Build stats for each deal
deal_data = []
for d in manager_deals:
    h_list = session.query(FundHolding).filter_by(deal_id=d.id).all()
    if h_list:
        latest_date = max(h.filing_date for h in h_list)
        latest = [h for h in h_list if h.filing_date == latest_date]
        total_par = sum(h.par_amount or 0 for h in latest)
        total_mv = sum(h.market_value or 0 for h in latest)
        price = (total_mv / total_par * 100) if total_par > 0 else None
        funds = sorted(set(h.source_fund for h in latest))
    else:
        total_par = 0
        total_mv = 0
        price = None
        funds = []
        latest = []
        h_list = []

    deal_data.append({
        "id": d.id, "name": d.deal_name, "base": get_base_name(d.deal_name),
        "par": total_par, "mv": total_mv, "price": price, "funds": funds,
        "all_holdings": h_list,
    })

# Group by base name
groups = OrderedDict()
for dd in deal_data:
    base = dd["base"]
    if base not in groups:
        groups[base] = []
    groups[base].append(dd)

groups = OrderedDict(
    sorted(groups.items(), key=lambda x: sum(d["par"] for d in x[1]), reverse=True)
)

st.markdown(f"**{selected_manager}** — {len(manager_deals)} deals in {len(groups)} series")


def _show_deal(dd):
    """Show deal detail within an expander."""
    if not dd["all_holdings"]:
        st.info("No holdings data.")
        return

    latest_date = max(h.filing_date for h in dd["all_holdings"])
    latest = [h for h in dd["all_holdings"] if h.filing_date == latest_date]

    total_par = sum(h.par_amount or 0 for h in latest)
    total_mv = sum(h.market_value or 0 for h in latest)
    avg_price = (total_mv / total_par * 100) if total_par > 0 else 0

    c1, c2, c3 = st.columns(3)
    c1.metric("Total Par", f"${total_par / 1e6:,.2f}M")
    c2.metric("Implied Price", f"{avg_price:.1f}¢" if avg_price > 0 else "N/A")
    c3.metric("Funds Holding", len(set(h.source_fund for h in latest)))

    rows = []
    for h in dd["all_holdings"]:
        price = (h.market_value / h.par_amount * 100) if h.par_amount and h.par_amount > 0 and h.market_value else None
        rows.append({
            "Fund": FUND_NAMES.get(h.source_fund, h.source_fund),
            "Filing Date": h.filing_date,
            "Par": f"${h.par_amount:,.0f}" if h.par_amount else "N/A",
            "Market Value": f"${h.market_value:,.0f}" if h.market_value else "N/A",
            "Price": f"{price:.1f}¢" if price else "N/A",
            "CUSIP": h.cusip or "",
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


# Step 2: Show grouped deals using expanders only
for base_name, group_deals in groups.items():
    group_par = sum(d["par"] for d in group_deals)
    group_par_str = f"${group_par / 1e6:,.1f}M" if group_par > 0 else ""

    if len(group_deals) == 1:
        dd = group_deals[0]
        par_str = f"${dd['par'] / 1e6:,.1f}M" if dd["par"] > 0 else "N/A"
        price_str = f"{dd['price']:.1f}¢" if dd["price"] else "N/A"
        funds_str = ", ".join(dd["funds"]) if dd["funds"] else "—"

        with st.expander(f"{dd['name']}  ·  Par: {par_str}  ·  Price: {price_str}  ·  {funds_str}"):
            _show_deal(dd)
    else:
        with st.expander(f"**{base_name}** — {len(group_deals)} deals  ·  Total Par: {group_par_str}"):
            for i, dd in enumerate(sorted(group_deals, key=lambda x: x["par"], reverse=True)):
                par_str = f"${dd['par'] / 1e6:,.1f}M" if dd["par"] > 0 else "N/A"
                price_str = f"{dd['price']:.1f}¢" if dd["price"] else "N/A"
                funds_str = ", ".join(dd["funds"]) if dd["funds"] else "—"

                st.markdown(f"#### {dd['name']}")
                st.caption(f"Par: {par_str}  ·  Price: {price_str}  ·  Held by: {funds_str}")
                _show_deal(dd)

                if i < len(group_deals) - 1:
                    st.markdown("---")


st.divider()
st.caption("All data sourced from SEC EDGAR NPORT-P filings.")
