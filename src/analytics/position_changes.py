"""
Quarter-over-quarter position change analytics.

Diffs a single fund's CLO holdings between two NPORT-P filing dates to surface
what changed: new positions, exited positions, and resized positions. Built on
the data already in `fund_holdings` (no new scraping required).

This is the reusable building block other features lean on (alerts, conviction
ranking). The Streamlit page in pages/6_Position_Changes.py is a thin view over it.
"""

from datetime import date
import re

import pandas as pd
from sqlalchemy.orm import Session

from src.analytics.vintage import parse_vintage
from src.models.schema import Deal, FundHolding


RESET_TOKENS = {"R", "RR", "R2", "2R", "R3", "3R", "RESET", "REFI", "REFINANCED", "REPRICED"}
ENTITY_TOKENS = {"LTD", "LIMITED", "LLC", "LP", "INC", "DAC", "CORP", "CORPORATION"}
NOTE_TOKENS = ("SECURED NOTE", "CLASS E", "CLASS F", "LOAN ACCUMULATION FACILITY")


def _deal_tokens(value) -> list[str]:
    """Name tokens without punctuation, legal wrappers, or reset decorations."""
    text = re.sub(r"[^A-Z0-9]+", " ", str(value or "").upper()).strip()
    return [token for token in text.split() if token not in ENTITY_TOKENS]


def _reset_key(value) -> str:
    """Conservative series key used only to find same-period reset candidates.

    A second, longer issuance identifier containing an already-seen year is
    dropped (e.g. ``... 2022-II 2022-2A``). Reset markers and an amended ``A``
    suffix are dropped, but distinct numeric series are deliberately retained.
    """
    tokens = [t for t in _deal_tokens(value) if t not in RESET_TOKENS]
    if len(tokens) >= 2 and tokens[-1] == "A" and any(c.isdigit() for c in tokens[-2]):
        tokens.pop()
    years = [i for i, token in enumerate(tokens) if re.fullmatch(r"20\d{2}", token)]
    if len(years) >= 2 and years[-1] > years[0]:
        # The second year and everything after it are the trustee/security suffix.
        tokens = tokens[:years[-1]]
    return " ".join(tokens)


def _price(row) -> float | None:
    value = row.get("price")
    return float(value) if value is not None and not pd.isna(value) else None


def classify_exit(row, added_df) -> tuple[str, str]:
    """Infer an exit type from the last NPORT mark and same-period additions.

    This is a heuristic, not evidence of a trustee call or an execution. Reset
    matching is intentionally strict: a related series must have the same
    manager (when disclosed) and the same normalized series name.
    """
    name = str(row.get("deal_name") or row.get("name_key") or "This deal")
    upper_name = name.upper()
    px = _price(row)
    vintage = parse_vintage(name)
    key = _reset_key(name)

    if not added_df.empty and key:
        candidates = added_df.copy()
        manager = row.get("manager")
        if manager and "manager" in candidates:
            candidates = candidates[candidates["manager"].fillna("").eq(manager)]
        for _, added in candidates.iterrows():
            added_name = str(added.get("deal_name") or added.get("name_key") or "")
            if key != _reset_key(added_name):
                continue
            if _deal_tokens(added_name) == _deal_tokens(name):
                continue  # punctuation / legal-wrapper variant, not a reset twin
            return ("Reset / refi", f"Same-series addition: {added_name}; likely reset or refinancing.")

    if any(token in upper_name for token in NOTE_TOKENS) and px is not None and px >= 90:
        return ("Call / liquidation", f"Note/facility exited at {px:.1f}¢; consistent with repayment or call.")
    if px is not None and px < 5:
        detail = f"Residual mark of {px:.2f}¢ before exit"
        if vintage is not None and vintage <= 2018:
            detail += f"; {vintage} vintage adds call/liquidation context"
        return ("Call / liquidation", detail + ".")
    if px is None:
        return ("Sale", "Position disappeared with no usable prior mark or same-series addition; treated as a discretionary exit.")
    return ("Sale", f"Exited at {px:.1f}¢ with no same-series addition; consistent with a secondary sale or discretionary exit.")


def get_filing_dates(session: Session, fund: str) -> list[date]:
    """All filing dates for a fund, most recent first."""
    rows = (
        session.query(FundHolding.filing_date)
        .filter(FundHolding.source_fund == fund)
        .distinct()
        .all()
    )
    return sorted({r[0] for r in rows if r[0]}, reverse=True)


def _holdings_frame(session: Session, fund: str, filing_date: date) -> pd.DataFrame:
    """One fund's holdings at a single filing date, keyed by deal_id."""
    rows = (
        session.query(FundHolding, Deal)
        .join(Deal, FundHolding.deal_id == Deal.id)
        .filter(
            FundHolding.source_fund == fund,
            FundHolding.filing_date == filing_date,
        )
        .all()
    )
    records = []
    for h, d in rows:
        par = h.par_amount or 0
        mv = h.market_value or 0
        price = (mv / par * 100) if par > 0 and mv else None
        records.append({
            "deal_id": d.id,
            "deal_name": d.deal_name,
            "name_key": d.name_key,
            "manager": d.manager,
            "par": par,
            "market_value": mv,
            "price": price,
            "cusip": h.cusip or "",
        })
    return pd.DataFrame(records)


def compute_position_changes(
    session: Session,
    fund: str,
    prev_date: date,
    curr_date: date,
    resize_threshold_pct: float = 1.0,
) -> dict:
    """
    Diff a fund's holdings between two filing dates.

    `prev_date` is the earlier filing, `curr_date` the later one. Positions whose
    par moves by less than `resize_threshold_pct` percent are treated as unchanged
    (filters out rounding noise in the disclosures).

    Returns a dict with:
      - added:   DataFrame of positions present at curr but not prev
      - exited:  DataFrame of positions present at prev but not curr, with inferred exit_type / exit_why
      - resized: DataFrame of positions in both whose par changed materially
      - unchanged_count: int
      - summary: dict of headline numbers
    """
    prev = _holdings_frame(session, fund, prev_date)
    curr = _holdings_frame(session, fund, curr_date)

    prev_ids = set(prev["deal_id"]) if not prev.empty else set()
    curr_ids = set(curr["deal_id"]) if not curr.empty else set()

    added_ids = curr_ids - prev_ids
    exited_ids = prev_ids - curr_ids
    common_ids = curr_ids & prev_ids

    added = (
        curr[curr["deal_id"].isin(added_ids)]
        [["deal_name", "name_key", "manager", "par", "price", "cusip"]]
        .sort_values("par", ascending=False)
        .reset_index(drop=True)
    )

    exited = (
        prev[prev["deal_id"].isin(exited_ids)]
        [["deal_name", "name_key", "manager", "par", "price", "cusip"]]
        .sort_values("par", ascending=False)
        .reset_index(drop=True)
    )
    if not exited.empty:
        labels = exited.apply(lambda row: classify_exit(row.to_dict(), added), axis=1)
        exited["exit_type"] = [label[0] for label in labels]
        exited["exit_why"] = [label[1] for label in labels]

    # Resized: merge common positions and compare par
    resized = pd.DataFrame()
    unchanged_count = 0
    if common_ids:
        p = prev[prev["deal_id"].isin(common_ids)].set_index("deal_id")
        c = curr[curr["deal_id"].isin(common_ids)].set_index("deal_id")
        merged = c[["deal_name", "manager", "par", "price"]].join(
            p[["par", "price"]], rsuffix="_prev"
        ).rename(columns={"par": "par_curr", "price": "price_curr",
                          "par_prev": "par_prev", "price_prev": "price_prev"})
        merged["par_change"] = merged["par_curr"] - merged["par_prev"]
        merged["par_pct_change"] = merged.apply(
            lambda r: (r["par_change"] / r["par_prev"] * 100) if r["par_prev"] else 0.0,
            axis=1,
        )
        merged["price_change"] = merged["price_curr"] - merged["price_prev"]

        material = merged["par_pct_change"].abs() >= resize_threshold_pct
        unchanged_count = int((~material).sum())
        resized = (
            merged[material]
            .reset_index()
            .sort_values("par_change", key=lambda s: s.abs(), ascending=False)
            .reset_index(drop=True)
        )

    summary = {
        "n_added": len(added),
        "n_exited": len(exited),
        "n_resized": len(resized),
        "n_unchanged": unchanged_count,
        "par_added": float(added["par"].sum()) if not added.empty else 0.0,
        "par_exited": float(exited["par"].sum()) if not exited.empty else 0.0,
        "net_par_change": (
            (float(curr["par"].sum()) if not curr.empty else 0.0)
            - (float(prev["par"].sum()) if not prev.empty else 0.0)
        ),
        "prev_positions": len(prev),
        "curr_positions": len(curr),
    }

    return {
        "added": added,
        "exited": exited,
        "resized": resized,
        "unchanged_count": unchanged_count,
        "summary": summary,
    }
