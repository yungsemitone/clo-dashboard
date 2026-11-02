"""Sanity-check a rebuilt CLO database against the previously committed one.

Usage: python scripts/check_scrape.py NEW_DB BASELINE_DB
Exit 0 if counts are acceptable, 1 otherwise.
Requires >= RATIO of baseline counts AND absolute floors.
"""
import os, sqlite3, sys

RATIO = float(os.environ.get("SCRAPE_MIN_RATIO", "0.90"))
# Floors reflect the post-dedup (name_key, Aug 2026) universe: ~648 deals / ~2,438
# holdings. The old 650 floor sat above the real count and failed every full scrape.
FLOORS = {"deals": 600, "fund_holdings": 2300}
FUNDS = ["ECC", "OCCI", "OXLC", "PDCC", "PRIF"]
FILINGS_PER_FUND = 4


def counts(path):
    out = {}
    try:
        c = sqlite3.connect(path)
        for t in FLOORS:
            out[t] = c.execute(f"select count(*) from {t}").fetchone()[0]
    except Exception as e:  # missing db/table -> zero
        print(f"warn: could not read {path}: {e}")
        out = {t: 0 for t in FLOORS}
    return out


def check(new, base):
    ok = True
    for t, floor in FLOORS.items():
        need = max(floor, int(base.get(t, 0) * RATIO))
        status = "OK" if new[t] >= need else "SHORT"
        print(f"{t}: new={new[t]} baseline={base.get(t, 0)} required>={need} {status}")
        ok &= new[t] >= need
    return ok


def check_filings(path):
    """Every fund must have its last 4 filings — catches one dropped quarter,
    which the ratio check alone misses (79 of 2,438 rows is only -3%)."""
    ok = True
    c = sqlite3.connect(path)
    for f in FUNDS:
        n = c.execute("select count(distinct filing_date) from fund_holdings where source_fund=?", (f,)).fetchone()[0]
        if n < FILINGS_PER_FUND:
            print(f"{f}: only {n}/{FILINGS_PER_FUND} filings SHORT")
            ok = False
    return ok


if __name__ == "__main__":
    new_db, base_db = sys.argv[1], sys.argv[2]
    ok = check(counts(new_db), counts(base_db))
    ok = check_filings(new_db) and ok
    sys.exit(0 if ok else 1)
