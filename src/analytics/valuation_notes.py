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


# Short, research-grounded read on each fund's mandate / "mindset". Sources:
# fund disclosures + sector commentary (Seeking Alpha CEF/CLO coverage).
FUND_STRATEGY = {
    "OXLC": "Oxford Lane — the largest public CLO-equity fund; an aggressive, high-payout, equity-heavy book.",
    "ECC": "Eagle Point — a CLO specialist that also issues its own (“Park”) CLOs; equity plus some CLO debt for steadiness.",
    "OCCI": "OFS Credit — a smaller, conservatively-levered fund (high asset coverage) holding CLO equity and mezzanine.",
    "PDCC": "Pearl Diver — a newer fund focused on secondary-market CLO equity and debt.",
    "PRIF": "Priority Income — a Prospect Capital affiliate with a large, diversified CLO equity / mezzanine book.",
}


def fund_book_posture(df) -> dict:
    """
    Each fund's overall marking level — par-weighted average implied price across
    its priced (>=1c) positions. A fund that marks its whole book low will mark
    any shared deal low too, so this separates 'house style' from deal views.
    df needs columns: fund, par, mv, price.
    """
    out = {}
    for fund, g in df.groupby("fund"):
        g = g[g["price"].notna() & (g["price"] >= 1)]
        par = g["par"].sum()
        if par > 0:
            avg = g["mv"].sum() / par * 100
            label = ("a deep-discount, conservatively-marked book" if avg < 35
                     else "a mid-marked book" if avg < 50
                     else "a richer-marked book")
            out[fund] = {"avg": float(avg), "label": label, "n": int(len(g))}
    return out


def pairwise_bias(df, fund_a: str, fund_b: str) -> dict | None:
    """
    Systematic mark difference between two funds across every deal they both hold.
    Returns {n, mean_diff} where mean_diff = avg(price_a - price_b). None if <2
    shared priced deals. df needs columns: deal_id, fund, price.
    """
    a = df[df["fund"] == fund_a][["deal_id", "price"]].dropna()
    b = df[df["fund"] == fund_b][["deal_id", "price"]].dropna()
    m = a.merge(b, on="deal_id", suffixes=("_a", "_b"))
    m = m[(m["price_a"] >= 1) & (m["price_b"] >= 1)]
    if len(m) < 2:
        return None
    # mean_diff = avg(fund_a mark - fund_b mark); positive => fund_a marks higher.
    return {"n": int(len(m)), "mean_diff": float((m["price_a"] - m["price_b"]).mean()),
            "fund_a": fund_a, "fund_b": fund_b}


def explain_cross_held(deal_name: str, manager: str, rows: list[dict],
                       fund_postures: dict | None = None,
                       pair_bias: dict | None = None,
                       fund_names: dict | None = None) -> dict:
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

    # --- 4. Portfolio-level inference: house style vs. deal-specific view ---
    # (only meaningful for the 2-fund case, which is every cross-held deal here)
    portfolio_context = ""

    def _name(f):
        return (fund_names or {}).get(f, f)

    # Only meaningful when both funds hold the SAME security — comparing a debt
    # mark to an equity mark (different tranches) tells you nothing about marking style.
    if cusip_case == "same" and len(priced) == 2 and fund_postures:
        pr = sorted(priced, key=lambda r: r["price"])
        lo_fund, hi_fund = pr[0]["fund"], pr[1]["fund"]
        lo_p, hi_p = pr[0]["price"], pr[1]["price"]
        this_gap = hi_p - lo_p
        pa, pb = fund_postures.get(lo_fund), fund_postures.get(hi_fund)

        if pa and pb:
            reasons.append(
                f"**House marking style.** {_name(hi_fund)} marks its overall book at "
                f"~{pb['avg']:.0f}¢ ({pb['label']}); {_name(lo_fund)} at ~{pa['avg']:.0f}¢ "
                f"({pa['label']}). A fund that marks everything more conservatively will carry "
                f"this deal lower too — so part of the gap is house style, not a deal-specific call."
            )
            portfolio_context += (
                f"{_name(hi_fund)} books-wide avg mark ~{pb['avg']:.0f}c; "
                f"{_name(lo_fund)} ~{pa['avg']:.0f}c. "
            )

        if pair_bias:
            n = pair_bias["n"]
            # mean_diff = avg(fund_a - fund_b); positive => fund_a habitually marks higher.
            higher_overall = pair_bias["fund_a"] if pair_bias["mean_diff"] > 0 else pair_bias["fund_b"]
            typ = abs(pair_bias["mean_diff"])
            if higher_overall == hi_fund:
                if this_gap > max(typ * 1.5, typ + 6):
                    tail = (f"wider than their usual ~{typ:.0f}¢ difference, so beyond "
                            f"{_name(hi_fund)}'s generally richer marks there looks to be some "
                            f"deal-specific optimism here (or extra caution from {_name(lo_fund)}).")
                else:
                    tail = (f"roughly in line with their usual ~{typ:.0f}¢ difference — this gap "
                            f"looks mostly like house style, not a deal-specific disagreement.")
            else:
                tail = (f"the reverse of their usual pattern (across those deals {_name(higher_overall)} "
                        f"normally marks higher), so this one reads as a genuine deal-specific "
                        f"difference of opinion.")
            reasons.append(
                f"**Deal-specific or house style?** Across the {n} deals {_name(lo_fund)} and "
                f"{_name(hi_fund)} both hold, {_name(higher_overall)} marks ~{typ:.0f}¢ higher on "
                f"average. This deal's {this_gap:.0f}¢ gap is {tail}"
            )
            portfolio_context += (
                f"Across {n} shared deals, {_name(higher_overall)} marks ~{typ:.0f}c higher on "
                f"average; this deal's gap is {this_gap:.0f}c. "
            )

    # --- 5. Mandates / mindset ---
    if fund_names is not None:
        strat = [FUND_STRATEGY[r["fund"]] for r in rows if r["fund"] in FUND_STRATEGY]
        if strat:
            reasons.append("**Mandates.** " + "  ".join(strat))

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

    return {"summary": summary, "reasons": reasons, "cusip_case": cusip_case,
            "portfolio_context": portfolio_context}


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
