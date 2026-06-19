"""
Cross-fund conviction analytics.

Ranks deals and managers by *breadth of ownership* across the five public CLO
equity funds. The thesis: when multiple independent funds hold the same deal — and
commit real par to it — that breadth is a collective conviction signal, distinct
from the valuation *disagreement* shown on the Cross-Fund page.

Uses the latest filing per fund (consistent with the rest of the dashboard) so the
picture reflects current positioning, not stale quarters. Self-contained: no new
scraping, just the data already in `fund_holdings`.
"""

import pandas as pd
from sqlalchemy.orm import Session

from src.models.schema import Deal, FundHolding


def latest_holdings(session: Session) -> pd.DataFrame:
    """
    Every fund's holdings as of its most recent filing, one row per (deal, fund).

    Columns: deal_id, deal_name, manager, fund, par, market_value, price.
    """
    rows = (
        session.query(FundHolding, Deal)
        .join(Deal, FundHolding.deal_id == Deal.id)
        .all()
    )
    if not rows:
        return pd.DataFrame(columns=[
            "deal_id", "deal_name", "manager", "fund", "par", "market_value", "price",
        ])

    # Latest filing date per fund
    fund_latest: dict[str, object] = {}
    for h, _ in rows:
        if h.filing_date and (h.source_fund not in fund_latest
                              or h.filing_date > fund_latest[h.source_fund]):
            fund_latest[h.source_fund] = h.filing_date

    records = []
    for h, d in rows:
        if h.filing_date != fund_latest.get(h.source_fund):
            continue
        par = h.par_amount or 0
        mv = h.market_value or 0
        price = (mv / par * 100) if par > 0 and mv else None
        records.append({
            "deal_id": d.id,
            "deal_name": d.deal_name,
            "manager": d.manager,
            "fund": h.source_fund,
            "par": par,
            "market_value": mv,
            "price": price,
        })
    return pd.DataFrame(records)


def deal_conviction(session: Session, holdings: pd.DataFrame | None = None) -> pd.DataFrame:
    """
    Per-deal conviction: how many funds hold it and how much par they commit.

    Sorted by fund count (primary) then total par (secondary) — the deals the
    broadest set of funds back with the most money rise to the top.

    Columns: deal_id, deal_name, manager, n_funds, funds, total_par, total_mv,
    avg_price (par-weighted).
    """
    if holdings is None:
        holdings = latest_holdings(session)
    if holdings.empty:
        return holdings

    grouped = holdings.groupby(["deal_id", "deal_name", "manager"]).agg(
        n_funds=("fund", "nunique"),
        funds=("fund", lambda x: ", ".join(sorted(x.unique()))),
        total_par=("par", "sum"),
        total_mv=("market_value", "sum"),
    ).reset_index()
    grouped["avg_price"] = (grouped["total_mv"] / grouped["total_par"] * 100).where(
        grouped["total_par"] > 0
    )
    return grouped.sort_values(
        ["n_funds", "total_par"], ascending=[False, False]
    ).reset_index(drop=True)


def manager_conviction(session: Session, holdings: pd.DataFrame | None = None) -> pd.DataFrame:
    """
    Per-manager conviction: how broadly a manager's deals are held across funds.

    `n_funds` is the number of distinct funds holding *any* of the manager's deals
    (breadth of acceptance). Sorted by that, then total par.

    Columns: manager, n_funds, funds, n_deals, total_par, total_mv, avg_price.
    """
    if holdings is None:
        holdings = latest_holdings(session)
    if holdings.empty:
        return holdings

    grouped = holdings.groupby("manager").agg(
        n_funds=("fund", "nunique"),
        funds=("fund", lambda x: ", ".join(sorted(x.unique()))),
        n_deals=("deal_id", "nunique"),
        total_par=("par", "sum"),
        total_mv=("market_value", "sum"),
    ).reset_index()
    grouped["avg_price"] = (grouped["total_mv"] / grouped["total_par"] * 100).where(
        grouped["total_par"] > 0
    )
    return grouped.sort_values(
        ["n_funds", "total_par"], ascending=[False, False]
    ).reset_index(drop=True)
