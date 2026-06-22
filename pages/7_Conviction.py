import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import pandas as pd
import plotly.express as px

from src.db import init_db, get_session
from src.analytics.conviction import latest_holdings, deal_conviction, manager_conviction
from src.ui import apply_chrome

import yaml
from pathlib import Path

st.set_page_config(page_title="Conviction Ranking", page_icon="🎯", layout="wide")
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

# Discrete green scale keyed by number of funds (more funds = darker = higher conviction)
FUND_COUNT_COLORS = {1: "#C8DAD3", 2: "#7BA098", 3: "#4A7A68", 4: "#2E5E4A", 5: "#CBA255"}

session = get_db()

st.title("🎯 Conviction Ranking")
st.caption("Breadth of ownership across the five funds — which managers and deals the most funds collectively back")

holdings = latest_holdings(session)
if holdings.empty:
    st.warning("No data available.")
    st.stop()

n_total_funds = holdings["fund"].nunique()
mc = manager_conviction(session, holdings)
dc = deal_conviction(session, holdings)

# ============================================================
# Manager conviction — the headline signal
# ============================================================
st.subheader("Manager Conviction")
st.caption(
    "How many of the five funds hold *any* deal from each manager. When independent "
    "funds all back the same manager, that breadth is a collective vote of confidence."
)

held_by_all = mc[mc["n_funds"] == n_total_funds]
held_by_4plus = mc[mc["n_funds"] >= 4]

m1, m2, m3 = st.columns(3)
m1.metric(f"Held by All {n_total_funds} Funds", len(held_by_all))
m2.metric("Held by 4+ Funds", len(held_by_4plus))
m3.metric("Managers Tracked", len(mc))

# Distribution: managers by fund count
dist = mc["n_funds"].value_counts().reindex(range(1, n_total_funds + 1), fill_value=0).reset_index()
dist.columns = ["n_funds", "managers"]
dist["label"] = dist["n_funds"].astype(str) + (" fund" + dist["n_funds"].apply(lambda n: "" if n == 1 else "s"))

dcol1, dcol2 = st.columns([1, 1.4])

with dcol1:
    fig = px.bar(dist, x="label", y="managers", labels={"label": "", "managers": "# Managers"})
    fig.update_traces(marker_color=[FUND_COUNT_COLORS[n] for n in dist["n_funds"]])
    fig.update_layout(height=380, margin=dict(l=10, r=10, t=30, b=10), showlegend=False)
    fig.update_yaxes(title="# Managers")
    st.markdown("**How widely held are managers?**")
    st.plotly_chart(fig, use_container_width=True)

with dcol2:
    # Top managers by par, colored by breadth
    top_mgr = mc.nlargest(15, "total_par").sort_values("total_par")
    fig = px.bar(
        top_mgr, x=top_mgr["total_par"] / 1e6, y="manager", orientation="h",
        color="n_funds", color_continuous_scale=["#C8DAD3", "#CBA255"],
        labels={"x": "Total Par ($M)", "y": "", "color": "# Funds"},
        range_color=[1, n_total_funds],
    )
    fig.update_layout(height=380, margin=dict(l=10, r=10, t=30, b=10))
    st.markdown("**Largest managers, shaded by how many funds hold them**")
    st.plotly_chart(fig, use_container_width=True)

# Manager table
mgr_display = mc.copy()
mgr_display["total_par"] = (mgr_display["total_par"] / 1e6).round(1)
mgr_display["avg_price"] = mgr_display["avg_price"].round(1)
mgr_display = mgr_display[["manager", "n_funds", "funds", "n_deals", "total_par", "avg_price"]].rename(columns={
    "manager": "Manager", "n_funds": "# Funds", "funds": "Held By",
    "n_deals": "Deals", "total_par": "Total Par ($M)", "avg_price": "Avg Price (¢)",
})
st.dataframe(mgr_display, use_container_width=True, hide_index=True, height=400)

st.divider()

# ============================================================
# Cross-held deals — the literal "held by the most funds"
# ============================================================
st.subheader("Most Widely Held Deals")

cross_held = dc[dc["n_funds"] >= 2].copy()
max_funds = int(dc["n_funds"].max())

st.caption(
    f"Individual deals held by 2+ funds. Overlap is naturally thin at the deal level — "
    f"each fund tends to own different vintages and tranches of the same managers — so the "
    f"widest-held deal sits with {max_funds} funds. For how these funds' marks *disagree* on "
    f"the same deal, see the **Cross-Fund Comparison** page."
)

c1, c2 = st.columns(2)
c1.metric("Deals Held by 2+ Funds", len(cross_held))
c2.metric("Most Funds on One Deal", max_funds)

if cross_held.empty:
    st.info("No deals are currently held by multiple funds.")
else:
    deal_display = cross_held.copy()
    deal_display["total_par"] = (deal_display["total_par"] / 1e6).round(2)
    deal_display["avg_price"] = deal_display["avg_price"].round(1)
    deal_display = deal_display[["deal_name", "manager", "n_funds", "funds", "total_par", "avg_price"]].rename(columns={
        "deal_name": "Deal", "manager": "Manager", "n_funds": "# Funds",
        "funds": "Held By", "total_par": "Total Par ($M)", "avg_price": "Avg Price (¢)",
    })
    st.dataframe(deal_display, use_container_width=True, hide_index=True, height=400)

st.divider()
st.caption("All data sourced from SEC EDGAR NPORT-P filings · latest filing per fund.")
