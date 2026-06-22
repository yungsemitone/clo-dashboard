"""
CLO vintage analytics.

Groups deals by origination year (the "vintage" parsed from the deal name) and
summarizes how each vintage is priced across the funds' latest filings. Useful
for seeing whether the market marks, say, 2021 CLO equity differently from 2018.

Caveat: many CLO platforms name deals with Roman numerals or running series
numbers (e.g. "Madison Park Funding XXIV", "Venture XX CLO") rather than a year,
so a meaningful share of deals have no parseable vintage. Those are excluded from
the per-vintage stats and reported separately, never bucketed into a fake year.
"""

import re

import pandas as pd
from sqlalchemy.orm import Session

from src.analytics.conviction import latest_holdings

# A 4-digit year in a sane CLO origination window. CLOs as an asset class are
# post-2000; cap a little past today to tolerate forward-dated deal names.
_VINTAGE_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")
_MIN_YEAR, _MAX_YEAR = 2000, 2030


def parse_vintage(deal_name: str) -> int | None:
    """Extract the origination year from a deal name, or None if absent."""
    if not deal_name:
        return None
    match = _VINTAGE_RE.search(deal_name)
    if not match:
        return None
    year = int(match.group(1))
    return year if _MIN_YEAR <= year <= _MAX_YEAR else None


def deal_vintages(session: Session, holdings: pd.DataFrame | None = None) -> pd.DataFrame:
    """
    One row per deal (aggregated across the funds holding it at their latest
    filing), with a `vintage` column. `vintage` is None for deals whose name
    carries no year.

    Columns: deal_id, deal_name, manager, total_par, total_mv, avg_price, vintage.
    """
    if holdings is None:
        holdings = latest_holdings(session)
    if holdings.empty:
        return pd.DataFrame(columns=[
            "deal_id", "deal_name", "manager", "total_par", "total_mv", "avg_price", "vintage",
        ])

    deals = holdings.groupby(["deal_id", "deal_name", "manager"]).agg(
        total_par=("par", "sum"),
        total_mv=("market_value", "sum"),
    ).reset_index()
    deals["avg_price"] = (deals["total_mv"] / deals["total_par"] * 100).where(deals["total_par"] > 0)
    deals["vintage"] = deals["deal_name"].map(parse_vintage)
    return deals


def vintage_summary(
    session: Session,
    holdings: pd.DataFrame | None = None,
    min_price: float = 1.0,
) -> pd.DataFrame:
    """
    Aggregate by vintage year. Price stats use only "priced" positions
    (avg_price >= `min_price`) so near-zero write-offs don't drag the averages.
    `avg_price` is par-weighted over those priced deals.

    Columns: vintage, n_deals, total_par, total_mv, avg_price, median_price.
    """
    deals = deal_vintages(session, holdings)
    deals = deals[deals["vintage"].notna()].copy()
    if deals.empty:
        return pd.DataFrame(columns=[
            "vintage", "n_deals", "total_par", "total_mv", "avg_price", "median_price",
        ])
    deals["vintage"] = deals["vintage"].astype(int)

    priced = deals[deals["avg_price"] >= min_price]

    rows = []
    for vintage, grp in deals.groupby("vintage"):
        p = priced[priced["vintage"] == vintage]
        weighted_price = (p["total_mv"].sum() / p["total_par"].sum() * 100) if p["total_par"].sum() > 0 else None
        rows.append({
            "vintage": int(vintage),
            "n_deals": len(grp),
            "total_par": float(grp["total_par"].sum()),
            "total_mv": float(grp["total_mv"].sum()),
            "avg_price": weighted_price,
            "median_price": float(p["avg_price"].median()) if not p.empty else None,
        })

    return pd.DataFrame(rows).sort_values("vintage").reset_index(drop=True)


def unknown_vintage_count(session: Session, holdings: pd.DataFrame | None = None) -> int:
    """How many deals have no parseable vintage (Roman-numeral / series naming)."""
    deals = deal_vintages(session, holdings)
    return int(deals["vintage"].isna().sum())
