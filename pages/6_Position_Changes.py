import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yaml

from src.analytics.position_changes import compute_position_changes, get_filing_dates
from src.db import get_session, init_db
from src.ui import EXIT_TYPE_COLORS, apply_chrome, page_intro

st.set_page_config(page_title="Position Changes", page_icon="🔄", layout="wide")

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


FUND_NAMES = {"OXLC": "Oxford Lane", "ECC": "Eagle Point",
              "OCCI": "OFS Credit", "PDCC": "Pearl Diver", "PRIF": "Priority Income"}

session = get_db()
page_intro("Position Changes",
           "See what each fund bought, sold, or resized—and whether disappearing deals look like sales, resets, or liquidations.",
           "🔄")

fund_options = {f"{name} ({ticker})": ticker for ticker, name in FUND_NAMES.items()}
fund_options = dict(sorted(fund_options.items()))
sel_col1, sel_col2, sel_col3 = st.columns([2, 1.5, 1.5])
with sel_col1:
    selected_label = st.selectbox("Fund", list(fund_options.keys()))
fund_ticker = fund_options[selected_label]

dates = get_filing_dates(session, fund_ticker)
if len(dates) < 2:
    st.info(f"{FUND_NAMES[fund_ticker]} has fewer than two filings on record—nothing to compare yet.")
    st.stop()

date_labels = {d.strftime("%b %d, %Y"): d for d in dates}
label_list = list(date_labels.keys())
with sel_col2:
    prev_label = st.selectbox("From (earlier filing)", label_list, index=min(1, len(label_list) - 1))
prev_date = date_labels[prev_label]
newer_labels = [lbl for lbl, d in date_labels.items() if d > prev_date]
if not newer_labels:
    st.info("No later filing exists after this one. Pick an earlier starting point.")
    st.stop()
with sel_col3:
    curr_label = st.selectbox("To (later filing)", newer_labels, index=0)
curr_date = date_labels[curr_label]

result = compute_position_changes(session, fund_ticker, prev_date, curr_date)
s = result["summary"]
added, exited, resized = result["added"], result["exited"], result["resized"]

st.caption(f"{FUND_NAMES[fund_ticker]} · {prev_label} → {curr_label}")
m1, m2, m3 = st.columns(3)
m1.metric("Exited deals", s["n_exited"])
m2.metric("Exited par", f"-${s['par_exited'] / 1e6:,.1f}M")
m3.metric("Net par change", f"{'-' if s['net_par_change'] < 0 else ''}${abs(s['net_par_change']) / 1e6:,.1f}M")
st.markdown(f"**{s['n_exited']} exits, {s['n_added']} adds, and {s['n_resized']} resizes; net par "
            f"{'down' if s['net_par_change'] < 0 else 'up'} ${abs(s['net_par_change']) / 1e6:,.1f}M.**")
st.caption(f"{s['curr_positions']} positions now ({s['prev_positions']} before); {s['n_unchanged']} others stayed within 1% par. "
           "Exits and additions are filing differences, not necessarily cash trades.")


def _fmt_simple(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["Par $M"] = (out["par"] / 1e6).round(2)
    out["Price ¢"] = out["price"].round(1)
    return out[["deal_name", "manager", "Par $M", "Price ¢"]].rename(columns={
        "deal_name": "Deal", "manager": "Manager",
    })


# The interview read: separate market sales from deals rolling off or resetting.
st.subheader(f"Why Deals Disappeared ({s['n_exited']})")
st.caption("Exit types are inferred from NPORT marks and deal names—not confirmed by trustee call notices or trade records.")
if exited.empty:
    st.info("No positions fully exited this period.")
else:
    breakdown = (exited.groupby("exit_type", as_index=False)
                 .agg(Deals=("deal_name", "count"), **{"Par $M": ("par", lambda x: x.sum() / 1e6)}))
    order = ["Call / liquidation", "Reset / refi", "Sale"]
    breakdown["order"] = breakdown["exit_type"].map({v: i for i, v in enumerate(order)})
    breakdown = breakdown.sort_values("order").drop(columns="order")
    breakdown["Par $M"] = breakdown["Par $M"].round(2)
    breakdown = breakdown.rename(columns={"exit_type": "Exit type"})
    b1, b2 = st.columns([1.15, 1.85])
    with b1:
        st.dataframe(breakdown, hide_index=True, width="stretch")
    with b2:
        fig = go.Figure(go.Bar(
            x=breakdown["Par $M"], y=breakdown["Exit type"], orientation="h",
            marker_color=[EXIT_TYPE_COLORS.get(t) for t in breakdown["Exit type"]],
            customdata=breakdown["Deals"],
            hovertemplate="%{y}<br>%{x:.1f}M par · %{customdata} deal(s)<extra></extra>",
        ))
        fig.update_layout(height=180, margin=dict(l=0, r=30, t=5, b=0),
                          xaxis_title="Exited par ($M)", yaxis_title="", showlegend=False)
        st.plotly_chart(fig, width="stretch")
    odd_prices = exited[exited["price"].gt(150)]
    if not odd_prices.empty:
        st.warning(f"{len(odd_prices)} exited line(s) have implied marks above 150¢. These may be non-par instruments "
                   "(for example Common Units); do not compare them with ordinary CLO-equity prices.")
    display = exited.copy()
    display["Par $M"] = (display["par"] / 1e6).round(2)
    display["Price ¢"] = display["price"].round(1)
    display["Why"] = display.apply(
        lambda r: ("Reset twin added" if r["exit_type"] == "Reset / refi"
                   else "Near-par note/facility" if r["exit_type"] == "Call / liquidation" and pd.notna(r["price"]) and r["price"] >= 90
                   else "Residual below 5¢" if r["exit_type"] == "Call / liquidation"
                   else "No usable prior mark" if pd.isna(r["price"])
                   else "No reset twin"), axis=1)
    display = display[["deal_name", "manager", "Par $M", "Price ¢", "exit_type", "Why"]].rename(columns={
        "deal_name": "Deal", "manager": "Manager", "exit_type": "Exit type",
    })
    st.dataframe(display, width="stretch", hide_index=True,
                 column_config={"Why": st.column_config.TextColumn(width="medium")})
    with st.expander("Full exit rationale"):
        selected_exit = st.selectbox("Deal", exited.deal_name.tolist(), key="exit_rationale_deal")
        rationale = exited.loc[exited.deal_name == selected_exit].iloc[0]
        st.markdown(f"**{rationale.exit_type}** — {rationale.exit_why}")

with st.expander(f"Added positions ({s['n_added']})", expanded=False):
    if added.empty:
        st.caption("No new positions this period.")
    else:
        st.dataframe(_fmt_simple(added), width="stretch", hide_index=True)

with st.expander(f"Resized positions ({s['n_resized']})", expanded=False):
    if resized.empty:
        st.caption("No material resizes this period.")
    else:
        rz = resized.copy()
        odd_prices = rz[rz["price_curr"].gt(150) | rz["price_prev"].gt(150)]
        if not odd_prices.empty:
            st.warning(f"{len(odd_prices)} resized line(s) include marks above 150¢, which are not comparable with ordinary CLO-equity prices.")
        for col in ("par_prev", "par_curr", "par_change"):
            rz[col] = (rz[col] / 1e6).round(2)
        rz["price_curr"] = rz["price_curr"].round(1)
        display = rz[["deal_name", "par_change", "par_curr", "par_prev", "price_curr"]].rename(columns={
            "deal_name": "Deal", "par_change": "Par Change $M", "par_curr": "Par Now $M",
            "par_prev": "Par Before $M", "price_curr": "Price Now ¢",
        })
        st.dataframe(display, width="stretch", hide_index=True)

with st.expander("Largest par moves — chart", expanded=False):
    moves = []
    for _, r in added.iterrows():
        moves.append({"deal": r["deal_name"], "change": r["par"], "type": "Added"})
    for _, r in exited.iterrows():
        moves.append({"deal": r["deal_name"], "change": -r["par"], "type": "Exited"})
    for _, r in resized.iterrows():
        moves.append({"deal": r["deal_name"], "change": r["par_change"], "type": "Resized"})
    if moves:
        moves_df = pd.DataFrame(moves)
        top_moves = moves_df.loc[moves_df["change"].abs().nlargest(15).index].sort_values("change")
        colors = {"Added": "#6FA368", "Exited": "#D6705F", "Resized": "#CBA255"}
        fig = go.Figure()
        for mtype in ["Added", "Exited", "Resized"]:
            sub = top_moves[top_moves["type"] == mtype]
            if not sub.empty:
                fig.add_trace(go.Bar(y=sub["deal"], x=sub["change"] / 1e6, orientation="h",
                                     name=mtype, marker_color=colors[mtype]))
        fig.update_layout(height=450, margin=dict(l=20, r=20, t=30, b=20),
                          xaxis_title="Par change ($M)", barmode="relative",
                          legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
        st.plotly_chart(fig, width="stretch")
    else:
        st.caption("No material moves in this comparison.")

with st.expander("How to read the exit labels"):
    st.markdown("""
- **Call / liquidation:** the last mark was below 5¢, or a note/facility disappeared near par. An old (2013–2018) vintage adds context but is not proof.
- **Reset / refi:** a matching manager and deal series reappeared in the same period's additions, often with an R/reset marker or expanded issuance suffix.
- **Sale:** the default when a mid-range mark disappeared without a same-series addition.

These are transparent heuristics from fund-reported NPORT holdings. They cannot prove a trustee call, liquidation, or secondary-market execution.
""")
st.caption("Source: SEC EDGAR NPORT-P filings. Positions are matched by normalized deal identity across filing dates.")
