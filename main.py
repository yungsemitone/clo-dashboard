"""
CLO Trustee Report Scraper - CLI Entry Point
"""

import os
import logging
from pathlib import Path

import click
import yaml

from src.db import init_db, get_session
from src.scrapers.us_bank import USBankScraper
from src.scrapers.bny_mellon import BNYMellonScraper
from src.scrapers.sec_edgar import SECEdgarScraper
from src.parsers.report_parser import ReportParser
from src.exporters.exporter import DataExporter
from src.analytics.manager_tracker import ManagerTracker


def load_config():
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def setup_logging(config):
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, config["logging"]["level"]),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(config["logging"]["file"]),
            logging.StreamHandler(),
        ],
    )


SCRAPERS = {
    "us_bank": USBankScraper,
    "bny_mellon": BNYMellonScraper,
    "sec_edgar": SECEdgarScraper,
}


@click.group()
@click.pass_context
def cli(ctx):
    """CLO Trustee Report Scraper - collect and analyze public CLO data."""
    config = load_config()
    setup_logging(config)
    ctx.ensure_object(dict)
    ctx.obj["config"] = config


@cli.command()
@click.option("--trustee", type=click.Choice(list(SCRAPERS.keys())), required=True,
              help="Which trustee portal to scrape.")
@click.option("--limit", type=int, default=None,
              help="Max number of reports to download.")
@click.pass_context
def scrape(ctx, trustee, limit):
    """Scrape trustee reports from a portal."""
    config = ctx.obj["config"]
    logger = logging.getLogger("cli.scrape")

    scraper_cls = SCRAPERS[trustee]
    scraper = scraper_cls(config)

    logger.info(f"Starting scrape: {trustee}")
    reports = scraper.scrape(limit=limit)
    logger.info(f"Downloaded {len(reports)} reports from {trustee}")

    # Parse and store
    session = get_session(config)
    parser = ReportParser(config)

    for report_path in reports:
        try:
            parsed = parser.parse(report_path)
            parser.store(parsed, session)
            logger.info(f"Parsed and stored: {report_path.name}")
        except Exception as e:
            logger.error(f"Failed to parse {report_path.name}: {e}")

    session.commit()
    session.close()
    click.echo(f"Done. Scraped and stored {len(reports)} reports from {trustee}.")


@cli.command()
@click.option("--input", "input_dir", type=click.Path(exists=True), default="data/raw",
              help="Directory of raw reports to parse.")
@click.pass_context
def parse(ctx, input_dir):
    """Parse downloaded reports and store in database."""
    config = ctx.obj["config"]
    session = get_session(config)
    parser = ReportParser(config)
    input_path = Path(input_dir)
    logger = logging.getLogger("cli.parse")

    pdf_files = list(input_path.glob("**/*.pdf"))
    click.echo(f"Found {len(pdf_files)} PDF files to parse.")

    success = 0
    for pdf_path in pdf_files:
        try:
            parsed = parser.parse(pdf_path)
            parser.store(parsed, session)
            success += 1
        except Exception as e:
            logger.error(f"Failed: {pdf_path.name} - {e}")

    session.commit()
    session.close()
    click.echo(f"Parsed {success}/{len(pdf_files)} reports.")


@cli.command()
@click.option("--format", "fmt", type=click.Choice(["csv", "excel", "both"]), default="both")
@click.option("--output", type=click.Path(), default=None)
@click.pass_context
def export(ctx, fmt, output):
    """Export database to CSV or Excel."""
    config = ctx.obj["config"]
    session = get_session(config)
    exporter = DataExporter(config, session)

    if fmt in ("csv", "both"):
        out = output or config["export"]["csv_dir"]
        exporter.to_csv(out)
        click.echo(f"CSV exported to {out}")

    if fmt in ("excel", "both"):
        out = output or config["export"]["excel_dir"]
        exporter.to_excel(out)
        click.echo(f"Excel exported to {out}")

    session.close()


@cli.command()
@click.option("--manager", type=str, default=None, help="Filter by manager name.")
@click.option("--top", type=int, default=20, help="Number of managers to show.")
@click.pass_context
def analyze(ctx, manager, top):
    """Analyze CLO manager performance."""
    config = ctx.obj["config"]
    session = get_session(config)
    tracker = ManagerTracker(session)

    if manager:
        report = tracker.manager_report(manager)
        click.echo(report)
    else:
        leaderboard = tracker.leaderboard(top_n=top)
        click.echo(leaderboard)

    session.close()


@cli.command("list-deals")
@click.option("--manager", type=str, default=None)
@click.option("--trustee", type=str, default=None)
@click.pass_context
def list_deals(ctx, manager, trustee):
    """List all deals in the database."""
    config = ctx.obj["config"]
    session = get_session(config)

    from src.models.schema import Deal
    query = session.query(Deal)

    if manager:
        query = query.filter(Deal.manager.ilike(f"%{manager}%"))
    if trustee:
        query = query.filter(Deal.trustee.ilike(f"%{trustee}%"))

    deals = query.order_by(Deal.manager, Deal.deal_name).all()

    if not deals:
        click.echo("No deals found.")
        return

    click.echo(f"\n{'Deal Name':<40} {'Manager':<30} {'Trustee':<15} {'Size ($M)':<10}")
    click.echo("-" * 95)
    for d in deals:
        size = f"{d.deal_size_mm:,.0f}" if d.deal_size_mm else "N/A"
        click.echo(f"{d.deal_name:<40} {d.manager:<30} {d.trustee:<15} {size:<10}")

    click.echo(f"\nTotal: {len(deals)} deals")
    session.close()


if __name__ == "__main__":
    # Ensure data directories exist
    for d in ["data/raw", "data/processed", "data/exports/csv", "data/exports/excel", "logs"]:
        Path(d).mkdir(parents=True, exist_ok=True)

    init_db(load_config())
    cli()
