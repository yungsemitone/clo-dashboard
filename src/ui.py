"""
Shared UI theming for the dashboard — a warm-dark editorial look.

`apply_chrome()` loads the display fonts and the small bit of CSS the native
Streamlit theme (in .streamlit/config.toml) doesn't cover: serif headings, mono
metric values, hiding the Filing Detail nav item, and tightening the top margin.
Call it once per page, right after set_page_config + the auth gate.

The color constants are the chart palette — bright enough to read on the dark
background (the old dark-green series were nearly invisible).
"""

import streamlit as st

# Palette (mirrors the Morning Desk design tokens)
BG = "#1A1513"
CARD = "#251D19"
LINE = "#3A2F28"
INK = "#F3ECE3"
INK_2 = "#C9BCAF"
INK_3 = "#B7A99C"
CLARET = "#C25361"
GOLD = "#CBA255"
GREEN = "#6FA368"   # "up"
RED = "#D6705F"     # "down"

# Discrete sequence for categorical charts, and a low→high scale for prices.
CHART_SEQ = [GOLD, CLARET, GREEN, "#4A90D9", INK_2, "#8E7CC3"]
PRICE_SCALE = [RED, GOLD, GREEN]   # distressed → premium

# Per-fund colors (used by the cross-fund / comparison dot plots)
FUND_COLORS = {
    "OXLC": GOLD, "ECC": CLARET, "OCCI": GREEN, "PDCC": "#4A90D9", "PRIF": INK_2,
}

_CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,600;9..144,700&family=Libre+Franklin:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap');

/* Tighten the top gap and hide the hidden Filing Detail page + Streamlit chrome */
.block-container {{ padding-top: 1.5rem; }}
[data-testid="stSidebarNav"] li:has(a[href*="Filing_Detail"]) {{ display: none; }}
.stDeployButton {{ display: none !important; }}
[data-testid="stDecoration"] {{ display: none !important; }}

/* Serif display headings (Fraunces), warm ink */
h1, h2, h3, h4,
[data-testid="stHeading"] h1, [data-testid="stHeading"] h2, [data-testid="stHeading"] h3 {{
    font-family: "Fraunces", Georgia, serif !important;
    letter-spacing: -0.01em;
    color: {INK} !important;
}}

/* Numbers feel like a terminal: mono, tabular */
[data-testid="stMetricValue"] {{
    font-family: "IBM Plex Mono", monospace !important;
    font-variant-numeric: tabular-nums;
    color: {INK} !important;
}}
[data-testid="stMetricLabel"] {{
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-size: 0.72rem !important;
    color: {INK_3} !important;
}}
[data-testid="stMetricDelta"] {{ font-family: "IBM Plex Mono", monospace !important; }}

/* Captions in the muted ink */
[data-testid="stCaptionContainer"], .stCaption {{ color: {INK_3} !important; }}

/* Readable evidence: larger labels and comfortable table rows. */
html, body, [class*="css"] {{ font-size: 16px; }}
[data-testid="stDataFrame"] [role="columnheader"] {{
    color: {INK} !important; font-size: 0.92rem !important; font-weight: 600 !important;
}}
[data-testid="stDataFrame"] [role="gridcell"] {{
    color: {INK} !important; font-size: 0.92rem !important;
    padding-top: 0.45rem !important; padding-bottom: 0.45rem !important;
}}
[data-testid="stWidgetLabel"] p, [data-testid="stRadio"] p {{ color: {INK_2} !important; }}
.js-plotly-plot .plotly text {{ font-size: 14px !important; }}
[data-testid="stSidebarNav"] a {{ padding-top: 0.35rem; padding-bottom: 0.35rem; }}
[data-testid="stSidebarNav"] a span {{ font-size: 0.96rem; }}

/* A clear page thesis and calmer spacing, without changing the brand. */
.clo-page-lead {{
    color: {INK_2};
    font-size: 1.02rem;
    line-height: 1.65;
    max-width: 1050px;
    margin: -0.15rem 0 1.25rem;
}}
[data-testid="stDataFrame"] {{ border: 1px solid {LINE}; border-radius: 10px; }}
[data-testid="stExpander"] {{ margin-bottom: 0.65rem; }}
[data-testid="stExpander"] summary p {{ color: {INK_2}; }}
.clo-exit-badge {{
    display: inline-block; padding: 0.2rem 0.55rem; border-radius: 999px;
    font-size: 0.78rem; font-weight: 600; margin: 0.12rem 0.25rem 0.12rem 0;
}}
</style>
"""


def apply_chrome():
    """Inject fonts + chrome CSS. Call once per page after the auth gate."""
    st.markdown(_CSS, unsafe_allow_html=True)


EXIT_TYPE_COLORS = {
    "Call / liquidation": GREEN,
    "Call / repayment": GREEN,
    "Reset / refi": GOLD,
    "Sale": CLARET,
}


def page_intro(title: str, lead: str, icon: str = ""):
    """Consistent title plus one plain-English reason to read the page."""
    st.title(f"{icon} {title}".strip())
    st.markdown(f'<p class="clo-page-lead">{lead}</p>', unsafe_allow_html=True)


def exit_badge(label: str) -> str:
    """Small inline badge for custom HTML summaries (tables use plain text)."""
    color = EXIT_TYPE_COLORS.get(label, INK_2)
    return (f'<span class="clo-exit-badge" style="color:{color};'
            f'background:{color}20;border:1px solid {color}55">{label}</span>')
