import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from src.db import init_db, get_session
from src.ui import apply_chrome
from src.analytics.manager_scorecard import (
    tracked_positions, manager_trajectories, period_label,
)

import yaml
from pathlib import Path

st.set_page_config(page_title="Manager Scorecard", page_icon="📈", layout="wide")
st.markdown("", unsafe_allow_html=True)

from src.auth import check_password
if not check_password():
    st.stop()
apply_chrome()


@st.cache_resource
def get_db():
    config_path = Path(__file__).parent.parent / "config.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)
    init_db(config)
    return get_session(config)


session = get_db()

st.title("📈 Manager Scorecard")
st.caption("How the funds have **re-marked** each manager's deals over time — not a snapshot, a trajectory")

pos = tracked_positions(session)
if pos.empty:
    st.warning("Not enough filing history to measure trajectories yet.")
    st.stop()

MIN_POS = st.slider("Minimum tracked positions per manager", 2, 10, 3,
                    help="Managers with only one or two tracked positions are noise, not a track record.")
traj = manager_trajectories(session, min_positions=MIN_POS, positions=pos)

overall = (pos["par_then"] * pos["delta"]).sum() / pos["par_then"].sum()
declined = (pos["delta"] < 0).mean() * 100

m1, m2, m3, m4 = st.columns(4)
m1.metric("Positions Tracked", len(pos))
m2.metric("Managers Scored", len(traj))
m3.metric("Market-Wide Mark Change", f"{overall:+.1f}¢",
          help="Par-weighted average change in implied price across every tracked position.")
m4.metric("Positions Marked Down", f"{declined:.0f}%")

st.caption(f"Lookback: **{period_label(pos)}**")

st.divider()

# --- The headline read ---
direction = "written down" if overall < 0 else "written up"
st.markdown(
    f"""<div style="background:#251D19;border-left:4px solid #CBA255;padding:1rem 1.2rem;
    border-radius:0 6px 6px 0;line-height:1.7;color:#C9BCAF;">
    Across <b>{len(pos)}</b> positions the five funds held at both ends of the window, CLO equity was
    {direction} by <b>{abs(overall):.1f}¢</b> per dollar of par on a par-weighted basis, with
    <b>{declined:.0f}%</b> of individual positions marked lower. That is a move in the asset class
    itself — the spread between managers below shows who fared better or worse within it.
    </div>""", unsafe_allow_html=True)

st.divider()

# --- Ranked trajectory chart ---
st.subheader("Mark Change by Manager")
st.caption("Par-weighted change in implied price, each fund's oldest filing → newest. Green = held up, red = marked down.")

if traj.empty:
    st.info("No manager has enough tracked positions at this threshold. Lower the slider above.")
else:
    top = traj.sort_values("delta")
    fig = go.Figure(go.Bar(
        y=top["manager"], x=top["delta"], orientation="h",
        marker_color=["#D6705F" if d < 0 else "#6FA368" for d in top["delta"]],
        marker_line_color="#1A1513", marker_line_width=1,
        customdata=top[["n_positions", "n_funds", "price_then", "price_now"]],
        hovertemplate=("<b>%{y}</b><br>%{x:+.1f}¢<br>"
                       "%{customdata[2]:.0f}¢ → %{customdata[3]:.0f}¢<br>"
                       "%{customdata[0]} positions · %{customdata[1]} funds<extra></extra>"),
    ))
    fig.update_layout(height=max(420, len(top) * 22), margin=dict(l=20, r=20, t=20, b=20),
                      xaxis_title="Change in implied price (¢ per $1 par)")
    st.plotly_chart(fig, use_container_width=True)

    st.divider()

    # --- Full scorecard ---
    st.subheader("Scorecard")
    disp = traj.copy()
    disp["par_then"] = (disp["par_then"] / 1e6).round(1)
    for col in ("price_then", "price_now", "delta", "pct_declined"):
        disp[col] = disp[col].round(1)
    disp = disp[["manager", "n_positions", "n_funds", "par_then", "price_then",
                 "price_now", "delta", "pct_declined", "n_written_off"]].rename(columns={
        "manager": "Manager", "n_positions": "Positions", "n_funds": "Funds",
        "par_then": "Par Tracked ($M)", "price_then": "Was (¢)", "price_now": "Now (¢)",
        "delta": "Δ (¢)", "pct_declined": "% Marked Down", "n_written_off": "Written Off",
    })
    st.dataframe(disp, use_container_width=True, hide_index=True, height=460)

    # --- Position-level detail ---
    st.divider()
    st.subheader("Positions Behind a Manager")
    pick = st.selectbox("Manager", traj["manager"].tolist())
    detail = pos[pos["manager"] == pick].copy()
    detail["par_then"] = (detail["par_then"] / 1e6).round(2)
    for col in ("price_then", "price_now", "delta"):
        detail[col] = detail[col].round(1)
    detail = detail[["deal_name", "fund", "par_then", "price_then", "price_now", "delta"]].rename(
        columns={"deal_name": "Deal", "fund": "Fund", "par_then": "Par ($M)",
                 "price_then": "Was (¢)", "price_now": "Now (¢)", "delta": "Δ (¢)"})
    st.dataframe(detail.sort_values("Δ (¢)"), use_container_width=True, hide_index=True)

st.divider()
st.caption(
    "**Method.** For each fund we compare its oldest and newest NPORT-P filing and keep positions "
    "present in both, so this measures re-marking rather than buying or selling. Changes are weighted "
    "by starting par, so trimming a position doesn't read as a price move. The five funds file on "
    "their own schedules, so the window is ragged (roughly 6–12 months per fund) rather than a single "
    "aligned period. Non-par lines (participation fees, common units) are excluded from price math. "
    "Positions written down to near zero are kept — that's the signal — and counted separately."
)
