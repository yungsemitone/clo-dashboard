"""
Side-by-side comparison of two funds' latest-filing portfolios.

Answers: which managers/deals do both funds hold, what's unique to each, and how
do their marks compare on shared exposure. Built on `latest_holdings` so it
reflects current positioning. Deal-level overlap is naturally thin (funds own
different vintages of the same managers — see conviction.py), so the manager-level
comparison is the substantive view.
"""

import pandas as pd
from sqlalchemy.orm import Session

from src.analytics.conviction import latest_holdings


def _fund_metrics(frame: pd.DataFrame) -> dict:
    par = float(frame["par"].sum())
    mv = float(frame["market_value"].sum())
    return {
        "positions": int(len(frame)),
        "n_managers": int(frame["manager"].nunique()),
        "total_par": par,
        "total_mv": mv,
        "avg_price": (mv / par * 100) if par > 0 else 0.0,
    }


def compare_funds(
    session: Session,
    fund_a: str,
    fund_b: str,
    holdings: pd.DataFrame | None = None,
) -> dict:
    """
    Compare two funds. Returns a dict with:
      - metrics_a / metrics_b: headline portfolio stats per fund
      - shared_managers: DataFrame of managers held by both (par/price each side)
      - only_a / only_b: manager names unique to each fund
      - shared_deals: DataFrame of individual deals both funds hold (price each side)
    """
    if holdings is None:
        holdings = latest_holdings(session)

    a = holdings[holdings["fund"] == fund_a]
    b = holdings[holdings["fund"] == fund_b]

    # --- Manager level ---
    a_mgr = a.groupby("manager").agg(par_a=("par", "sum"), mv_a=("market_value", "sum"))
    b_mgr = b.groupby("manager").agg(par_b=("par", "sum"), mv_b=("market_value", "sum"))
    merged = a_mgr.join(b_mgr, how="outer").fillna(0.0)

    shared = merged[(merged["par_a"] > 0) & (merged["par_b"] > 0)].copy()
    shared["price_a"] = (shared["mv_a"] / shared["par_a"] * 100).where(shared["par_a"] > 0)
    shared["price_b"] = (shared["mv_b"] / shared["par_b"] * 100).where(shared["par_b"] > 0)
    shared["combined_par"] = shared["par_a"] + shared["par_b"]
    shared = shared.sort_values("combined_par", ascending=False).reset_index()

    only_a = sorted(merged[(merged["par_a"] > 0) & (merged["par_b"] == 0)].index.tolist())
    only_b = sorted(merged[(merged["par_a"] == 0) & (merged["par_b"] > 0)].index.tolist())

    # --- Deal level ---
    a_deal = a[["deal_id", "deal_name", "manager", "par", "price"]].rename(
        columns={"par": "par_a", "price": "price_a"})
    b_deal = b[["deal_id", "par", "price"]].rename(columns={"par": "par_b", "price": "price_b"})
    shared_deals = a_deal.merge(b_deal, on="deal_id", how="inner")
    if not shared_deals.empty:
        shared_deals = (
            shared_deals.assign(_combined=shared_deals["par_a"] + shared_deals["par_b"])
            .sort_values("_combined", ascending=False)
            .drop(columns="_combined")
            .reset_index(drop=True)
        )

    return {
        "metrics_a": _fund_metrics(a),
        "metrics_b": _fund_metrics(b),
        "shared_managers": shared,
        "only_a": only_a,
        "only_b": only_b,
        "shared_deals": shared_deals,
    }
