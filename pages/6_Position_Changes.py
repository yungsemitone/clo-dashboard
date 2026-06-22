import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from src.db import init_db, get_session
from src.analytics.position_changes import get_filing_dates, compute_position_changes
from src.ui import apply_chrome

import yaml
from pathlib import Path

st.set_page_config(page_title="Position Changes", page_icon="🔄", layout="wide")
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

st.title("🔄 Position Changes")
st.caption("What each fund added, exited, and resized between quarterly NPORT-P filings")

# --- Fund selector ---
fund_options = {f"{name} ({ticker})": ticker for ticker, name in FUND_NAMES.items()}
fund_options = dict(sorted(fund_options.items()))

sel_col1, sel_col2, sel_col3 = st.columns([2, 1.5, 1.5])
with sel_col1:
    selected_label = st.selectbox("Fund", list(fund_options.keys()))
fund_ticker = fund_options[selected_label]

dates = get_filing_dates(session, fund_ticker)
if len(dates) < 2:
    st.info(f"{FUND_NAMES[fund_ticker]} has fewer than two filings on record — nothing to compare yet.")
    st.stop()

# Default comparison: two most recent filings. Let the user pick either endpoint.
date_labels = {d.strftime("%b %d, %Y"): d for d in dates}
label_list = list(date_labels.keys())

with sel_col2:
    curr_label = st.selectbox("To (later filing)", label_list, index=0)
curr_date = date_labels[curr_label]

# "From" options must be strictly older than the chosen "To"
older_labels = [lbl for lbl, d in date_labels.items() if d < curr_date]
if not older_labels:
    st.info("No earlier filing exists before the selected one. Pick a later filing as the endpoint.")
    st.stop()
with sel_col3:
    prev_label = st.selectbox("From (earlier filing)", older_labels, index=0)
prev_date = date_labels[prev_label]

result = compute_position_changes(session, fund_ticker, prev_date, curr_date)
s = result["summary"]
added, exited, resized = result["added"], result["exited"], result["resized"]

st.divider()
st.markdown(f"**{FUND_NAMES[fund_ticker]}** · changes from **{prev_label}** → **{curr_label}**")

# --- Summary metrics ---
m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("New Positions", s["n_added"], f"+${s['par_added'] / 1e6:,.1f}M par")
m2.metric("Exited", s["n_exited"], f"-${s['par_exited'] / 1e6:,.1f}M par", delta_color="inverse")
m3.metric("Resized", s["n_resized"])
m4.metric("Net Par Change", f"${s['net_par_change'] / 1e6:,.1f}M")
m5.metric("Positions", s["curr_positions"], f"{s['curr_positions'] - s['prev_positions']:+d} vs prior")

st.caption(f"{s['n_unchanged']} positions held roughly flat (par moved less than 1%).")

# --- Biggest moves chart ---
moves = []
for _, r in added.iterrows():
    moves.append({"deal": r["deal_name"], "change": r["par"], "type": "Added"})
for _, r in exited.iterrows():
    moves.append({"deal": r["deal_name"], "change": -r["par"], "type": "Exited"})
for _, r in resized.iterrows():
    moves.append({"deal": r["deal_name"], "change": r["par_change"], "type": "Resized"})

if moves:
    moves_df = pd.DataFrame(moves)
    moves_df["abs"] = moves_df["change"].abs()
    top_moves = moves_df.nlargest(15, "abs").sort_values("change")
    colors = {"Added": "#6FA368", "Exited": "#D6705F", "Resized": "#CBA255"}
    fig = go.Figure()
    for mtype in ["Added", "Exited", "Resized"]:
        sub = top_moves[top_moves["type"] == mtype]
        if not sub.empty:
            fig.add_trace(go.Bar(
                y=sub["deal"], x=sub["change"] / 1e6, orientation="h",
                name=mtype, marker_color=colors[mtype],
            ))
    fig.update_layout(
        height=450, margin=dict(l=20, r=20, t=30, b=20),
        xaxis_title="Par change ($M)", barmode="relative",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    st.subheader("Biggest Par Moves")
    st.plotly_chart(fig, use_container_width=True)

st.divider()


def _fmt_simple(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["par"] = (out["par"] / 1e6).round(2)
    out["price"] = out["price"].round(1)
    return out[["deal_name", "manager", "par", "price"]].rename(columns={
        "deal_name": "Deal", "manager": "Manager", "par": "Par ($M)", "price": "Price (¢)",
    })


# --- New positions ---
st.subheader(f"🟢 New Positions ({s['n_added']})")
if added.empty:
    st.caption("No new positions this period.")
else:
    st.dataframe(_fmt_simple(added), use_container_width=True, hide_index=True)

# --- Exited positions ---
st.subheader(f"🔴 Exited Positions ({s['n_exited']})")
if exited.empty:
    st.caption("No positions fully exited this period.")
else:
    st.dataframe(_fmt_simple(exited), use_container_width=True, hide_index=True)

# --- Resized positions ---
st.subheader(f"🔀 Resized Positions ({s['n_resized']})")
if resized.empty:
    st.caption("No material resizes this period.")
else:
    rz = resized.copy()
    rz["par_prev"] = (rz["par_prev"] / 1e6).round(2)
    rz["par_curr"] = (rz["par_curr"] / 1e6).round(2)
    rz["par_change"] = (rz["par_change"] / 1e6).round(2)
    rz["par_pct_change"] = rz["par_pct_change"].round(1)
    rz["price_curr"] = rz["price_curr"].round(1)
    rz["price_change"] = rz["price_change"].round(1)
    display = rz[["deal_name", "manager", "par_prev", "par_curr", "par_change",
                  "par_pct_change", "price_curr", "price_change"]].rename(columns={
        "deal_name": "Deal", "manager": "Manager",
        "par_prev": "Par Before ($M)", "par_curr": "Par After ($M)",
        "par_change": "Δ Par ($M)", "par_pct_change": "Δ Par (%)",
        "price_curr": "Price (¢)", "price_change": "Δ Price (¢)",
    })
    st.dataframe(display, use_container_width=True, hide_index=True, height=400)

st.divider()
st.caption("All data sourced from SEC EDGAR NPORT-P filings. Positions matched by deal across filing dates.")
