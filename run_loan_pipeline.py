"""
Refresh loan-level data for the Portfolio Shifts & Signals page.

Incremental per fund: a fund whose scrape fails keeps its previous rows, so a
flaky EDGAR response never wipes good data. Does NOT touch the CLO-tranche
tables that run_pipeline.py rebuilds.

    export PYTHONPATH=.
    python run_loan_pipeline.py
"""

import logging
from pathlib import Path

import yaml

from src.db import init_db, get_session
from src.models.schema import LoanHolding, ObligorIndustry
from src.scrapers.fred import refresh_macro
from src.scrapers.loan_nport_scraper import LOAN_FUNDS, LoanNPORTScraper
from src.scrapers.ncsr_industry import scrape_industries


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    config = yaml.safe_load(open(Path(__file__).parent / "config.yaml"))
    init_db(config)
    session = get_session(config)

    scraper = LoanNPORTScraper(n_filings=6)
    print("Loan NPORT-P:", scraper.run(session))
    print("N-CSR industries:", scrape_industries(scraper, session, LOAN_FUNDS))
    print("FRED:", refresh_macro(session))
    print(f"loan_holdings={session.query(LoanHolding).count()} "
          f"obligor_industry={session.query(ObligorIndustry).count()}")
    session.close()


if __name__ == "__main__":
    main()
