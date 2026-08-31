"""
Manager mark trajectories — how each manager's deals have been re-marked over time.

Every other page in this dashboard reads a single snapshot. This one uses the
filing *history*: for each fund we take its oldest and newest NPORT-P filing, find
the positions present in both, and measure how the funds moved the mark.

The headline metric is a par-weighted average price change, in cents:

    delta = Σ( par_then_i × (price_now_i − price_then_i) ) / Σ( par_then_i )

Weighting by the *starting* par isolates re-marking from position sizing — a fund
trimming a position doesn't register as a price move.

Caveat worth stating in the UI: the five funds file on their own schedules, so the
lookback is each fund's own oldest→newest span (roughly 6–12 months), not one
aligned window. Positions written down to near zero are kept in the trajectory —
a mark going from 40¢ to 0.5¢ is the single most important signal here — but are
also counted separately so they can be read on their own.
"""

import pandas as pd
from sqlalchemy.orm import Session

from src.models.schema import Deal, FundHolding
from src.analytics.pricing import is_par_priced

WRITE_OFF = 1.0  # cents; at or below this a position is effectively impaired


def _price(par, mv):
    return (mv / par * 100) if par and par > 0 and mv is not None else None


def tracked_positions(session: Session) -> pd.DataFrame:
    """
    One row per position that exists in both a fund's oldest and newest filing.

    Columns: fund, deal_id, deal_name, manager, date_then, date_now,
    par_then, price_then, price_now, delta.
    """
    rows = (
        session.query(FundHolding, Deal)
        .join(Deal, FundHolding.deal_id == Deal.id)
        .all()
    )
    if not rows:
        return pd.DataFrame()

    recs = [{
        "fund": h.source_fund, "deal_id": d.id, "deal_name": d.deal_name,
        "manager": d.manager, "filing_date": h.filing_date,
        "par": h.par_amount or 0, "mv": h.market_value or 0,
        "price": _price(h.par_amount, h.market_value),
    } for h, d in rows if h.filing_date]
    df = pd.DataFrame(recs)

    out = []
    for fund, g in df.groupby("fund"):
        dates = sorted(g["filing_date"].unique())
        if len(dates) < 2:
            continue
        then = g[g["filing_date"] == dates[0]].set_index("deal_id")
        now = g[g["filing_date"] == dates[-1]].set_index("deal_id")
        common = then.index.intersection(now.index)
        for did in common:
            t, n = then.loc[did], now.loc[did]
            # A deal can appear twice in one filing (two tranches); skip those
            # rather than guess which pairs with which.
            if isinstance(t, pd.DataFrame) or isinstance(n, pd.DataFrame):
                continue
            # Skip non-par instruments (participation fees, common units) — their
            # "price" isn't cents-on-par and would swamp the average. See pricing.py.
            if not (is_par_priced(t["price"]) and is_par_priced(n["price"])) or not t["par"]:
                continue
            out.append({
                "fund": fund, "deal_id": did, "deal_name": n["deal_name"],
                "manager": n["manager"], "date_then": dates[0], "date_now": dates[-1],
                "par_then": t["par"], "price_then": t["price"], "price_now": n["price"],
                "delta": n["price"] - t["price"],
            })
    return pd.DataFrame(out)


def manager_trajectories(session: Session, min_positions: int = 3,
                         positions: pd.DataFrame | None = None) -> pd.DataFrame:
    """
    Roll tracked positions up to the manager level.

    `min_positions` filters out managers with too little history to read anything
    into — with one or two positions the average is noise, not a track record.

    Columns: manager, n_positions, n_funds, par_then, price_then, price_now,
    delta, pct_declined, n_written_off.
    """
    pos = tracked_positions(session) if positions is None else positions
    if pos.empty:
        return pd.DataFrame()

    out = []
    for mgr, g in pos.groupby("manager"):
        if len(g) < min_positions:
            continue
        w = g["par_then"].sum()
        if w <= 0:
            continue
        out.append({
            "manager": mgr,
            "n_positions": int(len(g)),
            "n_funds": int(g["fund"].nunique()),
            "par_then": float(w),
            "price_then": float((g["par_then"] * g["price_then"]).sum() / w),
            "price_now": float((g["par_then"] * g["price_now"]).sum() / w),
            "delta": float((g["par_then"] * g["delta"]).sum() / w),
            "pct_declined": float((g["delta"] < 0).mean() * 100),
            "n_written_off": int(((g["price_now"] <= WRITE_OFF) &
                                  (g["price_then"] > WRITE_OFF)).sum()),
        })
    return pd.DataFrame(out).sort_values("delta", ascending=False).reset_index(drop=True)


def period_label(positions: pd.DataFrame) -> str:
    """Human-readable description of the (ragged) lookback window."""
    if positions.empty:
        return "no history"
    return (f"{positions['date_then'].min():%b %Y} – {positions['date_now'].max():%b %Y}"
            f" (each fund's own oldest → newest filing)")
