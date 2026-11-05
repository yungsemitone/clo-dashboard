"""
Portfolio Shifts & Signals — loan-level analytics.

Inputs are real NPORT-P loan positions (`loan_holdings`) from the public loan
funds of seven CLO managers, fund-reported industries (`obligor_industry`) and
FRED macro series (`macro_series`). Nothing here is simulated.

What we CAN'T see publicly (and therefore infer, clearly labelled as such):
ratings (no Moody's/S&P in NPORT), actual CLO OC/IC cushions, CCC buckets,
WARF/diversity and reinvestment-period dates of individual CLOs. The "likely
reason" labels below translate observable facts (price vs par, default/PIK
flags, maturity, breadth of selling, sector moves) into the CLO constraint that
most plausibly forced or motivated the trade.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.models.schema import obligor_match_key

# --- thresholds (CLO indenture conventions) --------------------------------
DISCOUNT_OBLIGATION_PX = 80.0   # bought/marked below ~80 -> carried at purchase price / MV in OC
CCC_PROXY_PX = 85.0             # loans sub-85 trade like CCC / B- negative watch
DISTRESSED_PX = 70.0
PAR_PX = 99.5
SIZE_CHANGE_TOL = 0.10          # +/-10% par change counts as upsize / trim
NEAR_MATURITY_YEARS = 1.5


def load_loans(session_or_conn) -> pd.DataFrame:
    conn = session_or_conn.get_bind() if hasattr(session_or_conn, "get_bind") else session_or_conn
    df = pd.read_sql("SELECT * FROM loan_holdings", conn, parse_dates=["report_date", "maturity", "filing_date"])
    ind = pd.read_sql("SELECT obligor_key, industry_raw, sector, source_fund FROM obligor_industry", conn)
    return attach_industry(df, ind)


def attach_industry(df: pd.DataFrame, ind: pd.DataFrame) -> pd.DataFrame:
    """Majority sector across funds that classify the borrower; raw label from first.

    Positions are re-keyed on `obligor_match_key` so the same borrower matches
    across funds that spell / truncate it differently."""
    df = df.copy()
    df["obligor_key"] = df["obligor_key"].map(obligor_match_key)
    # display name: the longest spelling seen for that borrower
    names = df.assign(_l=df.obligor.str.len()).sort_values("_l").groupby("obligor_key").obligor.last()
    df["obligor"] = df["obligor_key"].map(names)
    ind = ind.copy()
    if not ind.empty:
        ind["obligor_key"] = ind["obligor_key"].map(obligor_match_key)
    if ind.empty:
        df = df.copy()
        df["industry"] = "Unclassified"
        df["sector"] = "Unclassified"
        return df
    sec = (ind.groupby(["obligor_key", "sector"]).size().reset_index(name="n")
           .sort_values(["obligor_key", "n"], ascending=[True, False])
           .drop_duplicates("obligor_key")[["obligor_key", "sector"]])
    raw = ind.drop_duplicates("obligor_key")[["obligor_key", "industry_raw"]]
    out = df.merge(sec, on="obligor_key", how="left").merge(raw, on="obligor_key", how="left")
    out["sector"] = out["sector"].fillna("Unclassified")
    out["industry"] = out["industry_raw"].fillna("Unclassified")
    return out.drop(columns=["industry_raw"])


def obligor_positions(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate facilities to one row per (fund, report_date, obligor)."""
    g = df.assign(local_mv=df["price"] * df["balance"] / 100).groupby(
        ["fund", "manager", "report_date", "obligor_key"], as_index=False).agg(
        obligor=("obligor", "first"), par=("balance", "sum"), mv=("value_usd", "sum"),
        local_mv=("local_mv", "sum"), sector=("sector", "first"), industry=("industry", "first"),
        is_default=("is_default", "max"), is_pik=("is_pik", "max"),
        maturity=("maturity", "min"), coupon=("coupon", "mean"),
        facilities=("facility_key", lambda s: set(s)))
    g["price"] = np.where(g["par"] > 0, g["local_mv"] / g["par"] * 100, np.nan)
    return g.drop(columns=["local_mv"])


def snapshot_dates(df: pd.DataFrame, fund: str) -> list:
    return sorted(df.loc[df["fund"] == fund, "report_date"].unique())


def position_changes(pos: pd.DataFrame, fund: str, prev_date, curr_date) -> pd.DataFrame:
    """Diff one fund's obligor positions between two snapshots."""
    a = pos[(pos.fund == fund) & (pos.report_date == prev_date)].set_index("obligor_key")
    b = pos[(pos.fund == fund) & (pos.report_date == curr_date)].set_index("obligor_key")
    keys = a.index.union(b.index)
    rows = []
    for k in keys:
        pa, pb = (a.loc[k] if k in a.index else None), (b.loc[k] if k in b.index else None)
        ref = pb if pb is not None else pa
        par0 = float(pa.par) if pa is not None else 0.0
        par1 = float(pb.par) if pb is not None else 0.0
        px0 = float(pa.price) if pa is not None else np.nan
        px1 = float(pb.price) if pb is not None else np.nan
        if pa is None:
            action = "Add"
        elif pb is None:
            action = "Exit"
        elif par0 > 0 and (par1 - par0) / par0 > SIZE_CHANGE_TOL:
            action = "Upsize"
        elif par0 > 0 and (par1 - par0) / par0 < -SIZE_CHANGE_TOL:
            action = "Trim"
        else:
            action = "Hold"
        fac0 = pa.facilities if pa is not None else set()
        fac1 = pb.facilities if pb is not None else set()
        rows.append({
            "fund": fund, "manager": ref.manager, "obligor_key": k, "obligor": ref.obligor,
            "sector": ref.sector, "industry": ref.industry, "action": action,
            "par_prev": par0, "par_curr": par1, "par_change": par1 - par0,
            "price_prev": px0, "price_curr": px1, "price_change": px1 - px0,
            "is_default": int(ref.is_default), "is_pik": int(ref.is_pik),
            "maturity": ref.maturity, "coupon": ref.coupon,
            "facility_swap": bool(pa is not None and pb is not None and fac0 != fac1 and not (fac0 & fac1)),
            "prev_date": prev_date, "curr_date": curr_date,
        })
    return attach_exit_types(pd.DataFrame(rows))


def classify_loan_exit(row) -> str:
    """Coarse, inferred exit bucket for an Exit or Trim; empty otherwise.

    A full exit at ~par takes precedence over the detailed rotation read: it is
    much more consistent with repayment/refinancing than a discretionary sale.
    Near-maturity reductions use the same bucket. A facility swap means the
    borrower remains but the facility was refinanced or repriced.
    """
    if row.get("action") not in ("Exit", "Trim"):
        return ""
    if row.get("facility_swap"):
        return "Reset / refi"
    px = row.get("price_prev") if row.get("action") == "Exit" else row.get("price_curr")
    px = float(px) if px is not None and not pd.isna(px) else None
    mat, ref = row.get("maturity"), row.get("curr_date")
    yrs = ((pd.Timestamp(mat) - pd.Timestamp(ref)).days / 365.25
           if mat is not None and not pd.isna(mat) and ref is not None else None)
    if ((row.get("action") == "Exit" and px is not None and px >= PAR_PX)
            or (yrs is not None and yrs < NEAR_MATURITY_YEARS)):
        return "Call / repayment"
    return "Sale"


def attach_exit_types(changes: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with the three-way coarse label on sell-side actions."""
    out = changes.copy()
    if "exit_type" in out:
        out = out.drop(columns=["exit_type"])
    if out.empty:
        out["exit_type"] = pd.Series(dtype=str)
        return out
    labels = out.apply(classify_loan_exit, axis=1)
    insert_at = out.columns.get_loc("action") + 1 if "action" in out else len(out.columns)
    out.insert(insert_at, "exit_type", labels)
    return out


def latest_changes(pos: pd.DataFrame) -> pd.DataFrame:
    """Each fund's most recent snapshot vs its prior one, stacked."""
    out = []
    for fund in sorted(pos.fund.unique()):
        d = sorted(pos.loc[pos.fund == fund, "report_date"].unique())
        if len(d) >= 2:
            out.append(position_changes(pos, fund, d[-2], d[-1]))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def all_changes(pos: pd.DataFrame) -> pd.DataFrame:
    out = []
    for fund in sorted(pos.fund.unique()):
        d = sorted(pos.loc[pos.fund == fund, "report_date"].unique())
        for p, c in zip(d[:-1], d[1:]):
            out.append(position_changes(pos, fund, p, c))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def sector_price_moves(changes: pd.DataFrame) -> pd.DataFrame:
    """Median price change of continuing positions by sector (latest period)."""
    held = changes[changes.action.isin(["Hold", "Upsize", "Trim"]) & changes.price_change.notna()]
    return (held.groupby("sector").agg(median_px_change=("price_change", "median"),
                                        n=("obligor_key", "nunique"))
            .reset_index().sort_values("median_px_change"))


def likely_reason(row, sector_moves: dict | None = None, consensus_sellers: int = 0) -> tuple[str, str]:
    """(reason label, explanation) for a sell-side or price move.

    Rules are ordered from most mechanical (forced) to most discretionary.
    """
    px = row.get("price_curr") if row.get("action") != "Exit" else row.get("price_prev")
    px = px if px is not None and not pd.isna(px) else None
    dpx = row.get("price_change")
    sector_move = (sector_moves or {}).get(row.get("sector"))
    mat = row.get("maturity")
    ref_date = row.get("curr_date")
    yrs = ((pd.Timestamp(mat) - pd.Timestamp(ref_date)).days / 365.25
           if mat is not None and not pd.isna(mat) and ref_date is not None else None)
    selling = row.get("action") in ("Exit", "Trim")

    if row.get("is_default") or row.get("is_pik"):
        return ("Default / PIK",
                "Flagged defaulted or paying-in-kind. Defaulted obligations count at the lower of "
                "market value or recovery rate in CLO OC tests, and PIK loans often breach "
                "eligibility — managers are pushed to sell or work out.")
    if row.get("action") == "Exit" and row.get("facility_swap") is False and px is not None and px >= PAR_PX:
        if yrs is not None and yrs < NEAR_MATURITY_YEARS:
            return ("Repaid / near maturity",
                    f"Exited at ~par with only {yrs:.1f}y left — most likely repaid or refinanced, not sold.")
        return ("Repaid / refi or par rotation",
                "Exited at ~par. Usually a repayment/refinancing, or selling a tight-spread loan at par to "
                "rotate into wider new issue (spread/WAS management) — not a credit call.")
    if row.get("facility_swap"):
        return ("Refinanced / repriced",
                "Same borrower, new facility — an amend-&-extend, repricing or refinancing rolled the position.")
    if px is not None and px < DISTRESSED_PX:
        return ("Distressed (<70)",
                "Trading at distressed levels. In a CLO this is almost certainly CCC/Caa and in the CCC "
                "excess haircut; many managers exit before a default to stop the par hit becoming permanent.")
    if px is not None and px < DISCOUNT_OBLIGATION_PX:
        return ("Discount obligation zone (<80)",
                "Below ~80 a loan bought here is a 'discount obligation' (carried at purchase price, not par, in OC) "
                "and loans already held this low are typically CCC — every CLO's 7.5% CCC bucket caps how much "
                "they can own, so the excess is marked at MV. Holders are motivated sellers.")
    if selling and consensus_sellers >= 3 and px is not None and px < 95:
        return ("Consensus de-risking",
                f"{consensus_sellers} managers cut this borrower in the same quarter below 95 — consistent with "
                "coordinated de-risking ahead of a downgrade (CCC bucket / WARF protection).")
    if px is not None and px < CCC_PROXY_PX:
        return ("CCC-migration risk (80–85)",
                "Sub-85 marks usually mean B-/negative watch or already Caa1. Selling before a downgrade avoids "
                "eating CCC-bucket capacity and WARF headroom.")
    if dpx is not None and not pd.isna(dpx) and dpx <= -3 and sector_move is not None and sector_move <= -1:
        return ("Sector stress",
                f"Price fell {dpx:.1f} pts while the {row.get('sector')} sector median moved {sector_move:+.1f} — "
                "a macro/sector repricing rather than a single-name event.")
    if dpx is not None and not pd.isna(dpx) and dpx <= -3:
        return ("Idiosyncratic price drop",
                f"Price fell {dpx:.1f} pts vs a flat-ish sector — single-name news (earnings, leverage, LME risk).")
    if yrs is not None and yrs < NEAR_MATURITY_YEARS:
        return ("Near maturity / WAL",
                f"Only {yrs:.1f}y to maturity — refi risk and WAL tests drive rotation out of short paper.")
    if selling:
        return ("Discretionary / relative value",
                "No mechanical constraint visible — likely relative-value rotation (spread, size, concentration).")
    if row.get("action") in ("Add", "Upsize"):
        if px is not None and px < 90:
            return ("Buying the dip",
                    "Adding below 90 — a manager with CCC/OC room taking advantage of forced sellers.")
        return ("New issue / rotation in", "Adding near par — new issue or repricing allocation.")
    return ("Hold", "")


def consensus(changes: pd.DataFrame) -> pd.DataFrame:
    """Per obligor: how many managers bought vs sold in their latest quarter."""
    if changes.empty:
        return pd.DataFrame()
    c = changes.copy()
    c["buy"] = c.action.isin(["Add", "Upsize"])
    c["sell"] = c.action.isin(["Exit", "Trim"])
    g = c.groupby("obligor_key").agg(
        obligor=("obligor", "first"), sector=("sector", "first"), industry=("industry", "first"),
        holders_prev=("par_prev", lambda s: int((s > 0).sum())),
        holders_curr=("par_curr", lambda s: int((s > 0).sum())),
        buyers=("buy", "sum"), sellers=("sell", "sum"),
        buyer_list=("manager", lambda s: ", ".join(sorted(set(s[c.loc[s.index, "buy"]])))),
        seller_list=("manager", lambda s: ", ".join(sorted(set(s[c.loc[s.index, "sell"]])))),
        net_par_change=("par_change", "sum"),
        price_prev=("price_prev", "median"), price_curr=("price_curr", "median"),
        is_default=("is_default", "max"), is_pik=("is_pik", "max"),
    ).reset_index()
    g["price_change"] = g.price_curr - g.price_prev
    g["net"] = g.buyers - g.sellers
    return g


def price_momentum(pos: pd.DataFrame, periods: int = 2) -> pd.DataFrame:
    """Per obligor: average per-fund price change over the last 1 and `periods` snapshots."""
    rows = []
    for (fund, key), grp in pos.sort_values("report_date").groupby(["fund", "obligor_key"]):
        p = grp.price.dropna().values
        if len(p) < 2:
            continue
        rows.append({"obligor_key": key, "fund": fund, "chg_1q": p[-1] - p[-2],
                     "chg_nq": p[-1] - p[-1 - min(periods, len(p) - 1)], "px": p[-1]})
    m = pd.DataFrame(rows)
    if m.empty:
        return m
    return m.groupby("obligor_key").agg(mom_1q=("chg_1q", "mean"), mom_2q=("chg_nq", "mean"),
                                        funds_marking=("fund", "nunique")).reset_index()


def opportunity_screen(pos: pd.DataFrame, changes: pd.DataFrame, min_px=55.0, max_px=92.0) -> pd.DataFrame:
    """Loans whose discount looks technical (forced/constraint selling) rather than fundamental.

    Score components (all observable):
      +2 price sits in the CLO-constraint band (70–85): CCC bucket / discount-obligation sellers
      +1 per manager trimming/exiting this quarter (consensus supply), capped at 3
      +1 sector peers are NOT falling (sector median change > -1) -> not a sector call
      +1 still paying cash interest (no default / PIK flag)
      +1 at least one manager is ADDING (someone with room thinks it's cheap)
      -2 if price fell >10 pts in a quarter (fundamental-looking gap down)
    """
    if changes.empty:
        return pd.DataFrame()
    sm = sector_price_moves(changes).set_index("sector")["median_px_change"].to_dict()
    cons = consensus(changes)
    mom = price_momentum(pos)
    latest = (pos.sort_values("report_date").groupby("obligor_key").tail(1)
              [["obligor_key", "maturity", "coupon"]])
    cur_holders = (changes[changes.par_curr > 0].groupby("obligor_key")
                   .agg(par_held=("par_curr", "sum"), holders=("manager", "nunique")).reset_index())
    df = cons.merge(mom, on="obligor_key", how="left").merge(latest, on="obligor_key", how="left") \
             .merge(cur_holders, on="obligor_key", how="left")
    px = df.price_curr.fillna(df.price_prev)
    df["price"] = px
    df = df[(px >= min_px) & (px <= max_px)].copy()
    df["sector_move"] = df.sector.map(sm)
    s = np.where(df.price.between(DISTRESSED_PX, CCC_PROXY_PX), 2, 0)
    s = s + df.sellers.clip(upper=3)
    s = s + np.where(df.sector_move.fillna(0) > -1, 1, 0)
    s = s + np.where((df.is_default == 0) & (df.is_pik == 0), 1, 0)
    s = s + np.where(df.buyers > 0, 1, 0)
    s = s - np.where(df.mom_1q.fillna(0) < -10, 2, 0)
    df["technical_score"] = s
    df["discount_to_par"] = 100 - df.price

    def why(r):
        bits = []
        if DISTRESSED_PX <= r.price < DISCOUNT_OBLIGATION_PX:
            bits.append("sub-80: discount-obligation / CCC-excess haircut sellers")
        elif DISCOUNT_OBLIGATION_PX <= r.price < CCC_PROXY_PX:
            bits.append("80–85: CCC-migration risk, CLOs pre-emptively selling")
        if r.sellers:
            bits.append(f"{int(r.sellers)} mgr(s) selling: {r.seller_list}")
        if r.buyers:
            bits.append(f"{int(r.buyers)} mgr(s) buying: {r.buyer_list}")
        if not pd.isna(r.sector_move):
            bits.append(f"sector median {r.sector_move:+.1f} pts")
        if r.is_default or r.is_pik:
            bits.append("DEFAULT/PIK flag")
        return "; ".join(bits)

    df["signal_read"] = df.apply(why, axis=1)
    return df.sort_values(["technical_score", "discount_to_par"], ascending=[False, False])


def industry_mix(pos: pd.DataFrame, fund: str | None = None) -> pd.DataFrame:
    """Sector weights (by market value) per fund per snapshot."""
    d = pos if fund is None else pos[pos.fund == fund]
    g = d.groupby(["fund", "report_date", "sector"], as_index=False)["mv"].sum()
    g["weight"] = g["mv"] / g.groupby(["fund", "report_date"])["mv"].transform("sum") * 100
    return g


# Sector -> macro drivers (FRED series ids) and the one-line transmission story
SECTOR_DRIVERS = {
    "Technology & Software": (["NASDAQCOM", "SOFR"], "AI disruption risk to legacy SaaS multiples; high leverage on recurring revenue makes software sensitive to base rates."),
    "Healthcare": (["SOFR"], "Reimbursement (Medicare/Medicaid rates, No Surprises Act), labor costs; physician-practice & provider roll-ups are levered and rate-sensitive."),
    "Consumer": (["UMCSENT", "RSAFS"], "Real consumer spending, tariffs on goods, sentiment."),
    "Leisure, Travel & Restaurants": (["UMCSENT", "RSAFS"], "Discretionary spending and labor costs."),
    "Energy": (["DCOILWTICO"], "Oil & gas prices drive cash flows and asset coverage."),
    "Utilities & Power": (["DGS10"], "Power demand (data centers/AI) vs. long rates."),
    "Chemicals, Materials & Packaging": (["INDPRO", "DCOILWTICO"], "Industrial volumes, input costs (feedstock), destocking cycles."),
    "Industrials & Capital Goods": (["INDPRO"], "Industrial production, capex cycle, tariffs."),
    "Autos & Transportation": (["INDPRO", "DCOILWTICO"], "Auto production, freight volumes, fuel."),
    "Business & Professional Services": (["INDPRO", "SOFR"], "Corporate spend, staffing demand; AI substitution risk for labor-heavy services."),
    "Financials & Insurance": (["DGS10", "VIXCLS"], "Rates and market volumes (brokers, wealth); insurance brokers are serial acquirers."),
    "Media & Entertainment": (["UMCSENT"], "Ad spend and cord-cutting secular decline."),
    "Telecom & Cable": (["SOFR"], "Secular cord-cutting, fiber capex, high leverage."),
    "Aerospace & Defense": (["INDPRO"], "Defense budgets, air travel recovery, supply chain."),
    "Other": ([], ""),
    "Unclassified": ([], "Borrower not found in any fund's N-CSR industry schedule."),
}


def macro_changes(conn, start, end) -> pd.DataFrame:
    m = pd.read_sql("SELECT series_id, obs_date, value FROM macro_series", conn, parse_dates=["obs_date"])
    out = []
    for sid, g in m.groupby("series_id"):
        g = g.sort_values("obs_date")
        a = g[g.obs_date <= pd.Timestamp(start)].tail(1)
        b = g[g.obs_date <= pd.Timestamp(end)].tail(1)
        if a.empty or b.empty:
            continue
        v0, v1 = a.value.iloc[0], b.value.iloc[0]
        out.append({"series_id": sid, "start": v0, "end": v1, "change": v1 - v0,
                    "pct_change": (v1 / v0 - 1) * 100 if v0 else np.nan})
    return pd.DataFrame(out)
