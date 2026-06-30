"""
AI-powered report summarizer using the Anthropic API.

Generates concise, analyst-style summaries of CLO trustee report
snapshots, including period-over-period comparisons when available.
"""

import os
import json
import logging

logger = logging.getLogger(__name__)

# Default model for AI summaries. Override with CLO_SUMMARY_MODEL if you want to
# trade quality for cost (e.g. claude-sonnet-4-6 or claude-haiku-4-5).
DEFAULT_SUMMARY_MODEL = "claude-opus-4-8"


def _get_api_key() -> str:
    """Resolve the Anthropic API key from env or Streamlit secrets (lazy, so it
    works whether or not the key was set before this module was imported)."""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if key:
        return key
    try:
        import streamlit as st
        # Accept either casing in secrets.toml (env-var convention is uppercase).
        return st.secrets.get("ANTHROPIC_API_KEY") or st.secrets.get("anthropic_api_key") or ""
    except Exception:
        return ""


def ai_summaries_enabled() -> bool:
    """True if an API key is available, so callers can label the summary source."""
    return bool(_get_api_key())


def _summary_model() -> str:
    return os.environ.get("CLO_SUMMARY_MODEL", DEFAULT_SUMMARY_MODEL)


def generate_report_summary(
    snapshot: dict,
    previous_snapshot: dict | None = None,
) -> str:
    """
    Generate a natural-language summary of a CLO trustee report snapshot.

    Args:
        snapshot: dict of current report metrics
        previous_snapshot: dict of prior report metrics (for comparison)

    Returns:
        A 2-3 paragraph summary string.
    """
    api_key = _get_api_key()
    if not api_key:
        return _fallback_summary(snapshot, previous_snapshot)

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)

        system_prompt = (
            "You are a CLO credit analyst writing concise trustee report summaries. "
            "Write 2-3 short paragraphs covering: (1) OC/IC test health and cushion levels, "
            "(2) collateral quality trends (WARF, diversity, CCC bucket), and "
            "(3) any concerns or notable changes. Use precise numbers from the data. "
            "Be direct and analytical. No bullet points. No hedging language. "
            "If comparison data is provided, note meaningful changes."
        )

        user_content = f"Summarize this CLO trustee report snapshot:\n\n"
        user_content += f"Deal: {snapshot.get('deal_name', 'Unknown')}\n"
        user_content += f"Manager: {snapshot.get('manager', 'Unknown')}\n"
        user_content += f"Report Date: {snapshot.get('report_date', 'Unknown')}\n\n"
        user_content += f"Current Metrics:\n{json.dumps(snapshot, indent=2, default=str)}\n"

        if previous_snapshot:
            user_content += f"\nPrevious Report Metrics:\n{json.dumps(previous_snapshot, indent=2, default=str)}\n"
            user_content += "\nHighlight any meaningful changes between periods."

        message = client.messages.create(
            model=_summary_model(),
            max_tokens=500,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        )

        return next((b.text for b in message.content if b.type == "text"), "")

    except ImportError:
        logger.warning("anthropic package not installed. Using fallback summary.")
        return _fallback_summary(snapshot, previous_snapshot)
    except Exception as e:
        logger.error(f"API summary failed: {e}")
        return _fallback_summary(snapshot, previous_snapshot)


def _fallback_summary(snapshot: dict, previous: dict | None = None) -> str:
    """
    Rule-based fallback summary when the API is unavailable.
    Generates a readable summary from the raw metrics.
    """
    deal = snapshot.get("deal_name", "This deal")
    manager = snapshot.get("manager", "Unknown manager")
    report_date = snapshot.get("report_date", "")

    parts = []

    # Paragraph 1: OC/IC health
    oc_cushion = snapshot.get("senior_oc_cushion")
    oc_ratio = snapshot.get("senior_oc_ratio")
    oc_trigger = snapshot.get("senior_oc_trigger")
    mezz_cushion = snapshot.get("mezzanine_oc_cushion")

    p1 = f"As of {report_date}, {deal} (managed by {manager}) "
    if oc_ratio and oc_trigger:
        passing = "passes" if oc_cushion and oc_cushion > 0 else "fails"
        p1 += f"{passing} its senior OC test with a ratio of {oc_ratio:.2f}% against a {oc_trigger:.2f}% trigger"
        if oc_cushion is not None:
            p1 += f", leaving a {oc_cushion:.2f}% cushion"
        p1 += ". "
        if mezz_cushion is not None:
            status = "healthy" if mezz_cushion > 1.0 else "tight"
            p1 += f"The mezzanine OC cushion is {status} at {mezz_cushion:.2f}%. "
    else:
        p1 += "has limited OC test data in this report. "
    parts.append(p1)

    # Paragraph 2: Collateral quality
    warf = snapshot.get("warf")
    diversity = snapshot.get("diversity_score")
    ccc = snapshot.get("ccc_bucket_pct")
    defaulted = snapshot.get("defaulted_par")
    collateral = snapshot.get("collateral_par")

    p2 = ""
    if warf or diversity or ccc:
        metrics = []
        if warf:
            metrics.append(f"WARF of {warf:,.0f}")
        if diversity:
            metrics.append(f"diversity score of {diversity:.0f}")
        if ccc is not None:
            metrics.append(f"CCC bucket at {ccc:.1f}%")

        p2 = f"Collateral quality shows a {', '.join(metrics)}. "

        if defaulted and collateral and collateral > 0:
            default_rate = (defaulted / collateral) * 100
            p2 += f"The portfolio has {default_rate:.2f}% in defaulted assets "
            p2 += f"(${defaulted:,.0f} against ${collateral:,.0f} total par). "

    if p2:
        parts.append(p2)

    # Paragraph 3: Comparison if available
    if previous:
        changes = []
        prev_cushion = previous.get("senior_oc_cushion")
        if oc_cushion is not None and prev_cushion is not None:
            delta = oc_cushion - prev_cushion
            direction = "improved" if delta > 0 else "declined"
            changes.append(f"Senior OC cushion {direction} by {abs(delta):.2f}%")

        prev_warf = previous.get("warf")
        if warf and prev_warf:
            direction = "deteriorated" if warf > prev_warf else "improved"
            changes.append(f"WARF {direction} from {prev_warf:,.0f} to {warf:,.0f}")

        if changes:
            parts.append(f"Compared to the prior report ({previous.get('report_date', 'previous period')}): {'. '.join(changes)}.")

    return " ".join(parts) if parts else "Insufficient data for summary."


# ---------------------------------------------------------------------------
# Fund portfolio summaries — built from the NPORT-P portfolio data we actually
# have (positions, managers, prices), not from trustee reports. This is what the
# Fund Profiles page uses. With an API key it produces an analyst-style summary;
# without one it falls back to the rule-based paragraph below (unchanged output).
# ---------------------------------------------------------------------------


def generate_fund_summary(fund_data: dict) -> str:
    """
    Natural-language summary of a CLO equity fund's latest-filing portfolio.

    `fund_data` keys: fund_name, filing_date, n_positions, n_managers,
    total_par_mm, total_mv_mm, avg_price, median_price, top_managers
    (list of [name, par_mm]), top5_concentration_pct, largest_position
    ([name, par_mm]), n_above_50, n_below_20, n_priced.
    """
    api_key = _get_api_key()
    if not api_key:
        return _fallback_fund_summary(fund_data)

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)

        system_prompt = (
            "You are a CLO analyst writing a concise summary of a closed-end "
            "fund's CLO portfolio from its latest NPORT-P filing. Write one tight "
            "paragraph (3-5 sentences) covering portfolio size, manager "
            "concentration, and how the positions are priced (cents on the dollar "
            "of par). Use the exact numbers provided. Be direct and analytical — "
            "no bullet points, no preamble, no hedging, and do not invent data "
            "beyond what is given."
        )

        message = client.messages.create(
            model=_summary_model(),
            max_tokens=500,
            system=system_prompt,
            messages=[{"role": "user", "content": _fund_user_content(fund_data)}],
        )
        text = next((b.text for b in message.content if b.type == "text"), "").strip()
        return text or _fallback_fund_summary(fund_data)

    except ImportError:
        logger.warning("anthropic package not installed. Using fallback summary.")
        return _fallback_fund_summary(fund_data)
    except Exception as e:
        logger.error(f"Fund API summary failed: {e}")
        return _fallback_fund_summary(fund_data)


def _fund_user_content(d: dict) -> str:
    """Serialize the portfolio stats for the model."""
    top = "; ".join(f"{name} (${par:,.0f}M par)" for name, par in d.get("top_managers", []))
    largest = d.get("largest_position") or ("n/a", 0)
    return (
        f"Fund: {d.get('fund_name', 'Unknown')}\n"
        f"Filing date: {d.get('filing_date', 'Unknown')}\n"
        f"Positions: {d.get('n_positions', 0)} across {d.get('n_managers', 0)} managers\n"
        f"Total par: ${d.get('total_par_mm', 0):,.1f}M\n"
        f"Total market value: ${d.get('total_mv_mm', 0):,.1f}M\n"
        f"Weighted average implied price: {d.get('avg_price', 0):.1f} cents\n"
        f"Median position price: {d.get('median_price', 0):.1f} cents\n"
        f"Top managers by par: {top or 'n/a'}\n"
        f"Top 5 manager concentration: {d.get('top5_concentration_pct', 0):.0f}% of par\n"
        f"Largest single position: {largest[0]} (${largest[1]:,.1f}M par)\n"
        f"Priced positions above 50 cents: {d.get('n_above_50', 0)}\n"
        f"Priced positions below 20 cents: {d.get('n_below_20', 0)}\n"
        f"Total priced positions: {d.get('n_priced', 0)}\n"
    )


def _fallback_fund_summary(d: dict) -> str:
    """Rule-based portfolio summary when the API is unavailable."""
    fund_name = d.get("fund_name", "This fund")
    n_positions = d.get("n_positions", 0)
    n_managers = d.get("n_managers", 0)
    total_par = d.get("total_par_mm", 0)
    total_mv = d.get("total_mv_mm", 0)
    avg_price = d.get("avg_price", 0)
    top_managers = d.get("top_managers", [])
    largest = d.get("largest_position") or ("n/a", 0)
    above_50 = d.get("n_above_50", 0)
    below_20 = d.get("n_below_20", 0)

    if top_managers:
        names = [n for n, _ in top_managers[:3]]
        if len(names) >= 3:
            top_mgr_names = f"{names[0]}, {names[1]}, and {names[2]}"
        else:
            top_mgr_names = " and ".join(names)
        top_mgr_par = sum(p for _, p in top_managers[:3])
    else:
        top_mgr_names = "n/a"
        top_mgr_par = 0

    summary = (
        f"{fund_name} reported {n_positions} CLO positions across {n_managers} managers "
        f"in its NPORT-P filing dated {d.get('filing_date', 'an unknown date')}. The portfolio "
        f"has a total par value of ${total_par:,.1f}M with an aggregate market value of "
        f"${total_mv:,.1f}M, implying a weighted average price of {avg_price:.1f} cents per "
        f"dollar of par. The largest manager exposures are {top_mgr_names}, which together "
        f"account for ${top_mgr_par:,.0f}M in par. The fund's largest single position is "
        f"{largest[0]} at ${largest[1]:,.1f}M par. "
    )

    if above_50 > 0 or below_20 > 0:
        summary += (
            f"Across priced positions, {above_50} are marked above 50 cents and "
            f"{below_20} are marked below 20 cents, "
        )
        if below_20 > above_50:
            summary += "indicating the portfolio skews toward deeply discounted equity tranches."
        elif above_50 > below_20:
            summary += "indicating a portfolio weighted toward performing equity."
        else:
            summary += "reflecting a mix of performing and distressed positions."

    return summary


# ---------------------------------------------------------------------------
# Cross-fund valuation explainer — why one deal is marked differently by funds.
# Grounded in the marks + CUSIPs we pass; falls back to the rule-based notes
# from src/analytics/valuation_notes.py when no API key is set.
# ---------------------------------------------------------------------------


def generate_valuation_explainer(context: dict) -> str:
    """
    `context` keys: deal_name, manager, vintage, cusip_case ('same'/'different'/
    'unknown'), marks (list of {fund, price, cusip}), fallback (rule-based text).
    """
    api_key = _get_api_key()
    if not api_key:
        return context.get("fallback", "")

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)

        system_prompt = (
            "You are a CLO analyst explaining to a smart non-expert why a CLO is valued where it "
            "is, and why two funds mark the same deal differently. Write 2-4 short sentences in "
            "plain English. Ground every claim in the facts provided plus general CLO capital-"
            "structure knowledge (debt tranches near par, equity is the discounted first-loss "
            "residual; CLO positions are illiquid Level-3 marks; different CUSIPs mean different "
            "tranches). Do not invent specific metrics (OC tests, WARF, ratings) that aren't given. "
            "No preamble, no bullet points."
        )

        marks = context.get("marks", [])
        marks_txt = "; ".join(
            f"{m['fund']} marks it at {m['price']:.1f}¢ (CUSIP {m.get('cusip') or 'n/a'})"
            for m in marks if m.get("price") is not None
        )
        user = (
            f"Deal: {context.get('deal_name')}\n"
            f"Manager: {context.get('manager')}\n"
            f"Vintage: {context.get('vintage') or 'unknown'}\n"
            f"Same tranche across funds? {context.get('cusip_case')}\n"
            f"Marks: {marks_txt}\n"
            f"Portfolio context: {context.get('portfolio_context') or 'n/a'}\n\n"
            f"Explain why it's valued at these levels and why the funds differ. Use the portfolio "
            f"context to weigh how much of the gap is each fund's house marking style versus a "
            f"deal-specific view."
        )

        message = client.messages.create(
            model=_summary_model(),
            max_tokens=400,
            system=system_prompt,
            messages=[{"role": "user", "content": user}],
        )
        text = next((b.text for b in message.content if b.type == "text"), "").strip()
        return text or context.get("fallback", "")

    except ImportError:
        logger.warning("anthropic package not installed. Using fallback valuation note.")
        return context.get("fallback", "")
    except Exception as e:
        logger.error(f"Valuation explainer failed: {e}")
        return context.get("fallback", "")
