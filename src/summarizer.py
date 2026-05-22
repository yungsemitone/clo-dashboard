"""
AI-powered report summarizer using the Anthropic API.

Generates concise, analyst-style summaries of CLO trustee report
snapshots, including period-over-period comparisons when available.
"""

import os
import json
import logging

logger = logging.getLogger(__name__)

# Optional: set via environment variable or Streamlit secrets
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")


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
    if not ANTHROPIC_API_KEY:
        return _fallback_summary(snapshot, previous_snapshot)

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

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
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        )

        return message.content[0].text

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
