"""
Scrape real CLO deals from EDGAR NPORT-P filings.
No simulated data — only what we can actually pull from SEC filings.

Usage:
    export PYTHONPATH=.
    python run_pipeline.py
"""

from pathlib import Path
import yaml

from src.db import init_db, get_session
from src.models.schema import Deal, FundHolding, ReportSnapshot
from src.scrapers.nport_scraper import NPORTScraper


def main():
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    for d in ["data/raw", "data/processed", "data/exports/csv", "data/exports/excel", "logs"]:
        Path(d).mkdir(parents=True, exist_ok=True)

    init_db(config)
    session = get_session(config)

    # Clear old data
    print("Clearing old data...")
    session.query(FundHolding).delete()
    session.query(ReportSnapshot).delete()
    session.query(Deal).delete()
    session.commit()

    # Scrape real deals from EDGAR NPORT-P filings
    print("\n" + "=" * 60)
    print("Scraping real CLO deals from EDGAR NPORT-P filings")
    print("=" * 60)

    scraper = NPORTScraper(config)
    holdings = scraper.scrape()
    print(f"\nScraped {len(holdings)} CLO holdings")

    # Store in database (scraper handles dedup internally)
    print("\nStoring in database...")
    deals_created, holdings_stored = scraper.store(holdings, session)

    # Summary
    total_deals = session.query(Deal).count()
    total_holdings = session.query(FundHolding).count()
    unique_managers = session.query(Deal.manager).distinct().count()

    print("\n" + "=" * 60)
    print("DONE — all data below is real, scraped from SEC EDGAR")
    print("=" * 60)
    print(f"  Deals: {total_deals}")
    print(f"  Holdings: {total_holdings}")
    print(f"  Unique managers: {unique_managers}")
    print(f"\n  Run: streamlit run app.py")

    session.close()


if __name__ == "__main__":
    main()
