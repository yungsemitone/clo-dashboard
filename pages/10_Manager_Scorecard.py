import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from src.db import init_db, get_session
from src.ui import apply_chrome, page_intro
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

page_intro("Manager Scorecard", "See which managers' existing CLO positions held their marks best over time, separating valuation changes from buying and selling.", "📈")

pos = tracked_positions(session)
if pos.empty:
    st.warning("Not enough filing history to measure trajectories yet.")
    st.stop()

with st.expander("Filters and scope"):
    MIN_POS = st.slider("Minimum tracked positions per manager", 2, 10, 3,
                        help="Managers with only one or two tracked positions are noise, not a track record.")
    st.caption("Same-position re-marks only; each fund uses its own oldest and newest filing. Starting-par weighted; non-par instruments excluded.")
traj = manager_trajectories(session, min_positions=MIN_POS, positions=pos)

overall = (pos["par_then"] * pos["delta"]).sum() / pos["par_then"].sum()
declined = (pos["delta"] < 0).mean() * 100

m1, m2, m3 = st.columns(3)
m1.metric("Positions Tracked", len(pos))
m2.metric("Managers Scored", len(traj))
m3.metric("Market-Wide Mark Change", f"{overall:+.1f}¢",
          help="Par-weighted average change in implied price across every tracked position.")
st.caption(f"{declined:.0f}% of continuing positions were marked down · {period_label(pos)}")



# --- Ranked trajectory chart ---
st.subheader("Mark Change by Manager")
st.caption("Par-weighted change in implied price, each fund's oldest filing → newest. Green = held up, red = marked down.")

if traj.empty:
    st.info("No manager has enough tracked positions at this threshold. Lower the slider above.")
else:
    ranked = traj.sort_values("delta")
    top = pd.concat([ranked.head(5), ranked.tail(5)]).drop_duplicates("manager").sort_values("delta")
    st.caption("Top and bottom five managers; full ranking is optional below.")
    fig = go.Figure(go.Bar(
        y=top["manager"], x=top["delta"], orientation="h",
        marker_color=["#D6705F" if d < 0 else "#6FA368" for d in top["delta"]],
        marker_line_color="#1A1513", marker_line_width=1,
        customdata=top[["n_positions", "n_funds", "price_then", "price_now"]],
        text=top.delta.map(lambda d: f"{d:+.2f}¢"), textposition="outside", cliponaxis=False,
        hovertemplate=("<b>%{y}</b><br>%{x:+.2f}¢<br>"
                       "%{customdata[2]:.0f}¢ → %{customdata[3]:.0f}¢<br>"
                       "%{customdata[0]} positions · %{customdata[1]} funds<extra></extra>"),
    ))
    fig.add_vline(x=overall, line_dash="dash", line_color="#CBA255",
                  annotation_text=f"Market {overall:+.1f}¢", annotation_position="top")
    span = max(float(top.delta.max() - top.delta.min()), 1.0)
    pad = max(span * 0.14, 1.0)
    fig.update_layout(height=420, margin=dict(l=240, r=60, t=30, b=20),
                      xaxis_title="Starting-par-weighted mark change (¢)",
                      xaxis_range=[min(float(top.delta.min()), overall, 0.0) - pad,
                                   max(float(top.delta.max()), overall, 0.0) + pad])
    fig.update_yaxes(automargin=True)
    st.caption("Exact values are shown beside each bar, including near-zero changes.")
    st.plotly_chart(fig, width="stretch")
    with st.expander("Full manager ranking chart"):
        full_fig = go.Figure(go.Bar(y=ranked.manager, x=ranked.delta, orientation="h",
                                   marker_color=["#D6705F" if d < 0 else "#6FA368" for d in ranked.delta]))
        full_fig.update_layout(height=max(500, len(ranked) * 24), margin=dict(l=240, r=40, t=20, b=20),
                               xaxis_title="Starting-par-weighted mark change (¢)")
        st.plotly_chart(full_fig, width="stretch")

    st.divider()

    # --- Full scorecard ---
    st.subheader("Scorecard")
    disp = traj.copy()
    disp["par_then"] = (disp["par_then"] / 1e6).round(1)
    for col in ("price_then", "price_now", "delta", "pct_declined"):
        disp[col] = disp[col].round(1)
    then_label = f"Mark at First Filing ¢ ({pos.date_then.min():%b %Y} earliest)"
    now_label = f"Mark at Latest Filing ¢ ({pos.date_now.max():%b %Y} latest)"
    disp = disp[["manager", "n_positions", "par_then", "price_then",
                 "price_now", "delta", "pct_declined"]].rename(columns={
        "manager": "Manager", "n_positions": "Positions", "par_then": "Starting Par $M",
        "price_then": then_label, "price_now": now_label,
        "delta": "Mark Change ¢", "pct_declined": "% Marked Down",
    })
    st.dataframe(disp, use_container_width=True, hide_index=True, height=460)

    # --- Position-level detail ---
    st.divider()
    st.subheader("Positions Behind a Manager")
    pick = st.selectbox("Manager", traj["manager"].tolist())
    detail = pos[pos["manager"] == pick].copy()
    detail["First Filing"] = detail.date_then.map(lambda d: d.strftime("%b %d, %Y"))
    detail["Latest Filing"] = detail.date_now.map(lambda d: d.strftime("%b %d, %Y"))
    detail["par_then"] = (detail["par_then"] / 1e6).round(2)
    for col in ("price_then", "price_now", "delta"):
        detail[col] = detail[col].round(1)
    detail = detail[["deal_name", "fund", "First Filing", "Latest Filing", "par_then", "price_then", "price_now", "delta"]].rename(
        columns={"deal_name": "Deal", "fund": "Fund", "par_then": "Par $M",
                 "price_then": "First Mark ¢", "price_now": "Latest Mark ¢", "delta": "Mark Change ¢"})
    st.dataframe(detail.sort_values("Mark Change ¢"), width="stretch", hide_index=True)

with st.expander("Method and exclusions"):
    st.caption(
        "**Method.** For each fund we compare its oldest and newest NPORT-P filing and keep positions "
        "present in both, so this measures re-marking rather than buying or selling. Changes are weighted "
        "by starting par, so trimming a position doesn't read as a price move. The five funds file on "
        "their own schedules, so the window is ragged (roughly 6–12 months per fund) rather than a single "
    "aligned period. Non-par lines (participation fees, common units) are excluded from price math. "
        "Positions written down to near zero are kept — that's the signal — and counted separately."
    )
