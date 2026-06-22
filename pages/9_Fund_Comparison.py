import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from src.db import init_db, get_session
from src.analytics.fund_compare import compare_funds
from src.ui import apply_chrome

import yaml
from pathlib import Path

st.set_page_config(page_title="Fund Comparison", page_icon="⚖️", layout="wide")
apply_chrome()

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

st.title("⚖️ Fund Comparison")
st.caption("Two funds side by side — shared managers, where they overlap, and how their marks differ")

tickers = list(FUND_NAMES.keys())
labels = {f"{name} ({t})": t for t, name in FUND_NAMES.items()}
label_list = list(labels.keys())

c1, c2 = st.columns(2)
with c1:
    label_a = st.selectbox("Fund A", label_list, index=0)
fund_a = labels[label_a]
with c2:
    b_options = [lbl for lbl in label_list if labels[lbl] != fund_a]
    label_b = st.selectbox("Fund B", b_options, index=0)
fund_b = labels[label_b]

name_a, name_b = FUND_NAMES[fund_a], FUND_NAMES[fund_b]
A_COLOR, B_COLOR = "#CBA255", "#C25361"

result = compare_funds(session, fund_a, fund_b)
ma, mb = result["metrics_a"], result["metrics_b"]
shared = result["shared_managers"]
shared_deals = result["shared_deals"]

st.divider()

# --- Side-by-side headline metrics ---
col_a, col_b = st.columns(2)
with col_a:
    st.markdown(f"### {name_a}")
    s1, s2, s3 = st.columns(3)
    s1.metric("Positions", ma["positions"])
    s2.metric("Total Par", f"${ma['total_par'] / 1e6:,.0f}M")
    s3.metric("Avg Price", f"{ma['avg_price']:.1f}¢")
    st.caption(f"{ma['n_managers']} managers")
with col_b:
    st.markdown(f"### {name_b}")
    s1, s2, s3 = st.columns(3)
    s1.metric("Positions", mb["positions"])
    s2.metric("Total Par", f"${mb['total_par'] / 1e6:,.0f}M")
    s3.metric("Avg Price", f"{mb['avg_price']:.1f}¢")
    st.caption(f"{mb['n_managers']} managers")

st.divider()

# --- Overlap summary ---
o1, o2, o3, o4 = st.columns(4)
o1.metric("Shared Managers", len(shared))
o2.metric(f"Only {fund_a}", len(result["only_a"]))
o3.metric(f"Only {fund_b}", len(result["only_b"]))
o4.metric("Shared Deals", len(shared_deals))

st.divider()

# --- Shared managers ---
st.subheader("Shared Managers")
if shared.empty:
    st.info("These two funds hold no managers in common.")
else:
    st.caption("Managers both funds hold, by combined par. Price columns show where they mark the same manager differently.")

    # Grouped bar of top shared managers by par
    top = shared.head(12).iloc[::-1]
    fig = go.Figure()
    fig.add_trace(go.Bar(y=top["manager"], x=top["par_a"] / 1e6, orientation="h",
                         name=name_a, marker_color=A_COLOR))
    fig.add_trace(go.Bar(y=top["manager"], x=top["par_b"] / 1e6, orientation="h",
                         name=name_b, marker_color=B_COLOR))
    fig.update_layout(
        height=max(380, len(top) * 32), barmode="group",
        margin=dict(l=20, r=20, t=30, b=20), xaxis_title="Par ($M)",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    st.plotly_chart(fig, use_container_width=True)

    tbl = shared.copy()
    tbl["par_a"] = (tbl["par_a"] / 1e6).round(1)
    tbl["par_b"] = (tbl["par_b"] / 1e6).round(1)
    tbl["price_a"] = tbl["price_a"].round(1)
    tbl["price_b"] = tbl["price_b"].round(1)
    tbl = tbl[["manager", "par_a", "price_a", "price_b", "par_b"]].rename(columns={
        "manager": "Manager",
        "par_a": f"{fund_a} Par ($M)", "price_a": f"{fund_a} Price (¢)",
        "price_b": f"{fund_b} Price (¢)", "par_b": f"{fund_b} Par ($M)",
    })
    st.dataframe(tbl, use_container_width=True, hide_index=True, height=400)

# --- Shared deals ---
st.divider()
st.subheader("Shared Deals")
if shared_deals.empty:
    st.info(
        f"{name_a} and {name_b} hold no individual CLO deals in common — they own different "
        f"vintages and tranches of the same managers. The overlap lives at the manager level above."
    )
else:
    sd = shared_deals.copy()
    sd["par_a"] = (sd["par_a"] / 1e6).round(2)
    sd["par_b"] = (sd["par_b"] / 1e6).round(2)
    sd["price_a"] = sd["price_a"].round(1)
    sd["price_b"] = sd["price_b"].round(1)
    sd = sd[["deal_name", "manager", "par_a", "price_a", "price_b", "par_b"]].rename(columns={
        "deal_name": "Deal", "manager": "Manager",
        "par_a": f"{fund_a} Par ($M)", "price_a": f"{fund_a} Price (¢)",
        "price_b": f"{fund_b} Price (¢)", "par_b": f"{fund_b} Par ($M)",
    })
    st.dataframe(sd, use_container_width=True, hide_index=True)

# --- Unique managers ---
st.divider()
u1, u2 = st.columns(2)
with u1:
    with st.expander(f"Managers only in {name_a} ({len(result['only_a'])})"):
        st.write(", ".join(result["only_a"]) if result["only_a"] else "—")
with u2:
    with st.expander(f"Managers only in {name_b} ({len(result['only_b'])})"):
        st.write(", ".join(result["only_b"]) if result["only_b"] else "—")

st.divider()
st.caption("All data sourced from SEC EDGAR NPORT-P filings · latest filing per fund.")
