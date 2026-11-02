"""Sanity-check a rebuilt CLO database against the previously committed one.

Usage: python scripts/check_scrape.py NEW_DB BASELINE_DB
Exit 0 if counts are acceptable, 1 otherwise.
Requires >= RATIO of baseline counts AND absolute floors.
"""
import os, sqlite3, sys

RATIO = float(os.environ.get("SCRAPE_MIN_RATIO", "0.90"))
FLOORS = {"deals": 650, "fund_holdings": 2300}


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


if __name__ == "__main__":
    new_db, base_db = sys.argv[1], sys.argv[2]
    sys.exit(0 if check(counts(new_db), counts(base_db)) else 1)
