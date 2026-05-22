"""
Seed the database with realistic CLO demo data.

Run this to populate the dashboard for demonstration purposes
while the real scraper is being tuned against live EDGAR data.

Usage:
    python seed_data.py
"""

import random
from datetime import date, timedelta
from pathlib import Path

import yaml

from src.db import init_db, get_session
from src.models.schema import Deal, ReportSnapshot

# Realistic CLO deal data based on public market information
MANAGERS = [
    "Ares Management",
    "CIFC Asset Management",
    "Carlyle Group",
    "GSO Capital Partners",
    "Oak Hill Advisors",
    "PGIM Fixed Income",
    "Sound Point Capital",
    "Sculptor Capital",
    "Octagon Credit Investors",
    "Golub Capital",
    "Blackstone Credit",
    "KKR Credit Advisors",
    "Apollo Global Management",
    "Bain Capital Credit",
    "Owl Rock Capital",
]

TRUSTEES = ["US Bank", "BNY Mellon", "Computershare", "Wells Fargo"]

# Generate realistic deal names
def make_deal_name(manager: str, idx: int) -> str:
    short = manager.split()[0]
    suffixes = ["CLO", "Loan Fund", "Credit Fund", "Funding"]
    suffix = random.choice(suffixes)
    vintage = random.choice(["2021", "2022", "2023", "2024"])
    series = random.choice(["I", "II", "III", "IV", "V", ""])
    num = random.choice(["", f"-{random.randint(1, 5)}"])
    return f"{short} {suffix} {vintage}{'-' + series if series else ''}{num}".strip()


def generate_snapshot(deal_id: int, report_date: date, prev=None) -> ReportSnapshot:
    """Generate a realistic report snapshot with optional drift from previous."""

    # If we have a previous snapshot, drift from it (realistic time series)
    if prev:
        senior_oc = max(100, prev["senior_oc"] + random.gauss(0, 0.5))
        mezz_oc = max(100, prev["mezz_oc"] + random.gauss(0, 0.7))
        warf = max(2000, prev["warf"] + random.gauss(5, 30))
        diversity = max(40, prev["diversity"] + random.gauss(0, 1.5))
        ccc_pct = max(0, min(15, prev["ccc_pct"] + random.gauss(0.05, 0.3)))
        default_par = max(0, prev["default_par"] + random.gauss(50000, 200000))
    else:
        senior_oc = random.gauss(128, 5)
        mezz_oc = random.gauss(115, 6)
        warf = random.gauss(2850, 150)
        diversity = random.gauss(75, 8)
        ccc_pct = random.gauss(4.5, 2.0)
        default_par = max(0, random.gauss(2_000_000, 1_500_000))

    senior_oc_trigger = 120.0
    mezz_oc_trigger = 108.0
    collateral_par = random.gauss(450_000_000, 50_000_000)
    was = random.gauss(340, 30)  # bps
    wal = random.gauss(4.5, 0.8)
    equity_dist = max(0, random.gauss(800_000, 200_000))

    snap = ReportSnapshot(
        deal_id=deal_id,
        report_date=report_date,
        senior_oc_ratio=round(senior_oc, 2),
        senior_oc_trigger=senior_oc_trigger,
        senior_oc_cushion=round(senior_oc - senior_oc_trigger, 2),
        mezzanine_oc_ratio=round(mezz_oc, 2),
        mezzanine_oc_trigger=mezz_oc_trigger,
        mezzanine_oc_cushion=round(mezz_oc - mezz_oc_trigger, 2),
        senior_ic_ratio=round(random.gauss(180, 15), 2),
        senior_ic_trigger=120.0,
        mezzanine_ic_ratio=round(random.gauss(150, 12), 2),
        mezzanine_ic_trigger=110.0,
        warf=round(warf, 0),
        warf_limit=3200,
        was=round(was, 0),
        was_minimum=300,
        diversity_score=round(diversity, 0),
        diversity_minimum=60,
        wal=round(wal, 2),
        wal_limit=7.0,
        collateral_par=round(collateral_par, 0),
        defaulted_par=round(max(0, default_par), 0),
        ccc_bucket_pct=round(max(0, ccc_pct), 2),
        interest_proceeds=round(random.gauss(5_000_000, 500_000), 0),
        principal_proceeds=round(random.gauss(3_000_000, 800_000), 0),
        equity_distribution=round(equity_dist, 0),
        source_file="seed_data",
    )

    # Return snapshot and state for next iteration
    state = {
        "senior_oc": senior_oc,
        "mezz_oc": mezz_oc,
        "warf": warf,
        "diversity": diversity,
        "ccc_pct": ccc_pct,
        "default_par": default_par,
    }

    return snap, state


def main():
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    # Ensure data dirs exist
    for d in ["data/raw", "data/processed", "data/exports/csv", "data/exports/excel", "logs"]:
        Path(d).mkdir(parents=True, exist_ok=True)

    init_db(config)
    session = get_session(config)

    # Clear existing seed data
    session.query(ReportSnapshot).filter(ReportSnapshot.source_file == "seed_data").delete()
    session.query(Deal).filter(Deal.source_url == "seed_data").delete()
    session.commit()

    print("Seeding CLO database...")

    deal_count = 0
    snap_count = 0

    for mgr in MANAGERS:
        n_deals = random.randint(2, 5)
        for i in range(n_deals):
            deal_name = make_deal_name(mgr, i)

            # Skip if already exists
            if session.query(Deal).filter_by(deal_name=deal_name).first():
                continue

            deal = Deal(
                deal_name=deal_name,
                manager=mgr,
                trustee=random.choice(TRUSTEES),
                deal_size_mm=round(random.gauss(450, 80), 0),
                original_close_date=date(
                    random.randint(2020, 2024),
                    random.randint(1, 12),
                    random.randint(1, 28),
                ),
                status="active",
                source_url="seed_data",
            )
            session.add(deal)
            session.flush()
            deal_count += 1

            # Generate 6-18 monthly snapshots going back in time
            n_snapshots = random.randint(6, 18)
            base_date = date.today()
            state = None

            snapshots = []
            for j in range(n_snapshots):
                report_date = base_date - timedelta(days=30 * (n_snapshots - j))
                snap, state = generate_snapshot(deal.id, report_date, state)
                snapshots.append(snap)
                snap_count += 1

            session.add_all(snapshots)

    session.commit()
    session.close()

    print(f"Done. Created {deal_count} deals with {snap_count} report snapshots.")
    print("Run `streamlit run app.py` to view the dashboard.")


if __name__ == "__main__":
    main()
