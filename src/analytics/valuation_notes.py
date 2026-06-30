"""
Plain-English reasoning about *why* a CLO position is priced where it is, and
why two funds can mark the same deal differently.

Everything here is grounded in what the NPORT-P data actually shows — the marks,
the par, and crucially the CUSIPs (which reveal whether two funds hold the same
tranche or different rungs of the CLO's capital structure) — plus general CLO
structure. No fabricated metrics: we don't have OC/IC tests or tranche ratings,
so the notes stay at the level the data and domain support.
"""

from src.analytics.vintage import parse_vintage

_MISSING = {"", "N/A", "NA", "NONE", "—", "-"}


def _valid_cusip(c) -> str:
    c = (c or "").strip().upper()
    return "" if c in _MISSING else c


def price_band(p: float) -> str:
    """A short clause describing what an implied price level implies about the position."""
    if p is None:
        return "unpriced"
    if p < 1:
        return ("effectively written off — treated as impaired, which for CLO equity usually "
                "means the deal is past its reinvestment period or its overcollateralization (OC) "
                "tests have failed and are diverting cash away from the junior tranches")
    if p < 20:
        return ("a deeply distressed equity-style mark — a residual claim with little expected "
                "remaining cash flow")
    if p < 50:
        return ("a discounted CLO-equity mark — the equity is the first-loss, residual claim on "
                "the deal's excess cash, valued on expected future distributions and very sensitive "
                "to loan defaults and spreads")
    if p < 80:
        return ("a mid-range mark — either healthy equity that is still distributing well, or a "
                "mezzanine (BB/B) debt tranche")
    return ("a near-par mark — characteristic of a rated debt tranche (AAA–BBB) or strong, "
            "recent-vintage equity expected to be repaid close to in full")


def explain_cross_held(deal_name: str, manager: str, rows: list[dict]) -> dict:
    """
    rows: list of {fund, price, cusip, par} for one deal across the funds holding it.
    Returns {summary, reasons (list of markdown strings), cusip_case}.
    """
    priced = [r for r in rows if r.get("price") and r["price"] >= 1]
    cusips = {_valid_cusip(r.get("cusip")) for r in rows}
    cusips.discard("")
    n_funds = len(rows)
    n_with_cusip = sum(1 for r in rows if _valid_cusip(r.get("cusip")))
    vintage = parse_vintage(deal_name)

    reasons: list[str] = []

    # --- 1. Same tranche or different? (the biggest driver) ---
    if len(cusips) >= 2:
        cusip_case = "different"
        reasons.append(
            "**Different tranches of the same CLO.** The funds report different CUSIPs, so they "
            "hold different securities within this deal — almost always different rungs of the "
            "capital structure. Rated debt tranches (AAA down to BB) are paid first and mark close "
            "to par; the equity tranche is paid last, absorbs the first losses, and trades at a "
            "steep discount. So most of this gap is a capital-structure difference, not a "
            "disagreement about the deal's health."
        )
    elif len(cusips) == 1 and n_with_cusip == n_funds:
        cusip_case = "same"
        reasons.append(
            "**Same security, different marks.** The funds hold the identical tranche (matching "
            "CUSIP), so the gap isn't about owning different pieces — it's a genuine valuation "
            "difference. CLO equity and mezzanine tranches are illiquid, Level-3 assets with no "
            "screen price, so each fund marks to its own model or third-party pricing service. "
            "Independent managers can legitimately value the same position several cents apart "
            "depending on their pricing source, assumptions, and the date the mark was struck."
        )
    else:
        cusip_case = "unknown"
        reasons.append(
            "**Possibly different tranches.** Not every position here discloses a CUSIP, so we "
            "can't confirm it, but a gap like this usually means the funds hold different tranches "
            "— debt near par, equity at a discount — rather than marking the same security "
            "differently."
        )

    # --- 2. What the price levels themselves imply ---
    if len(priced) >= 2:
        lo = min(r["price"] for r in priced)
        hi = max(r["price"] for r in priced)
        reasons.append(
            f"**What the levels mean.** The ~{lo:.0f}¢ mark is {price_band(lo)}. "
            f"The ~{hi:.0f}¢ mark is {price_band(hi)}."
        )
    elif len(priced) == 1:
        p = priced[0]["price"]
        reasons.append(f"**What the level means.** The ~{p:.0f}¢ mark is {price_band(p)}.")

    # --- 3. Vintage context ---
    if vintage:
        reasons.append(
            f"**Vintage ({vintage}).** Older CLOs tend to mark lower as they amortise and exit "
            f"their reinvestment period, while newer deals sit closer to par — see the Vintage "
            f"page for the trend across years."
        )

    # --- Headline ---
    if len(priced) >= 2:
        lo = min(r["price"] for r in priced)
        hi = max(r["price"] for r in priced)
        spread = hi - lo
        cause = {
            "different": "mostly because the funds hold different tranches",
            "same": "on the identical tranche — a pure mark-to-model difference",
            "unknown": "likely because the funds hold different tranches",
        }[cusip_case]
        summary = (
            f"{deal_name} ({manager}) is held by {n_funds} funds at {lo:.0f}–{hi:.0f}¢, "
            f"a {spread:.0f}¢ spread — {cause}."
        )
    else:
        summary = f"{deal_name} ({manager}) is held by {n_funds} funds."

    return {"summary": summary, "reasons": reasons, "cusip_case": cusip_case}


PRIMER = """
**How to read these valuations**

A **CLO** (collateralized loan obligation) buys a pool of ~200 leveraged corporate loans and
funds them by selling a stack of claims on the loans' cash flows:

- **Debt tranches (AAA → BB)** are paid first, in order of seniority. They carry low risk and
  mark **close to par (~90–100¢)**.
- **The equity tranche** is paid *last* — it's the residual, first-loss claim on whatever cash is
  left after the debt is served. It's where the leverage and the upside live, so it's volatile and
  usually trades at a **deep discount (often ~20–60¢)**. The five funds here are mostly *CLO equity*
  funds.

**Why two funds can mark the *same* deal differently:**

1. **They hold different tranches.** If the CUSIPs differ, the funds own different rungs of the same
   CLO — one may hold debt (near par) and another the equity (discounted). Most large gaps are this.
2. **Genuine mark differences.** If the CUSIP is the *same*, it's the identical security — CLO
   positions are illiquid, "Level 3" assets with no live market price, so each fund marks to its own
   model or pricing vendor. Several cents of difference is normal.
3. **Timing & vintage.** Marks are struck on each fund's own filing date, and older vintages amortise
   to lower prices over time.
"""
