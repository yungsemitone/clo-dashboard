"""
Scraper for BNY Mellon's Global Corporate Trust investor reporting portal.

BNY Mellon hosts CLO trustee reports through their GCT Investor Reporting
platform. Reports typically require navigating a deal search interface.
"""

import re
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from src.scrapers.base import BaseScraper


class BNYMellonScraper(BaseScraper):
    """Scrape CLO trustee reports from BNY Mellon's GCT portal."""

    def __init__(self, config: dict):
        super().__init__(config)
        self.trustee_config = config["trustees"]["bny_mellon"]
        self.base_url = self.trustee_config["base_url"]

    def scrape(self, limit: int = None) -> list[Path]:
        """
        Scrape BNY Mellon trustee reports.

        Strategy:
        1. Search the GCT portal for CLO deals
        2. Navigate to each deal's report page
        3. Download the most recent trustee report PDFs

        The GCT portal is often JavaScript-heavy, so this may require
        Selenium for full functionality. This implementation handles
        the basic HTML case.
        """
        downloaded = []
        self.logger.info("Starting BNY Mellon scrape...")

        try:
            # Step 1: Search for CLO deals
            # The GCT portal typically has a search/filter interface
            search_url = f"{self.base_url}/search"
            resp = self._request(search_url, params={
                "dealType": "CLO",
                "assetClass": "Structured Finance",
            })

            listings = self.parse_listing(resp.text)

            if limit:
                listings = listings[:limit]

            self.logger.info(f"Found {len(listings)} report listings")

            # Step 2: Download reports
            for listing in listings:
                try:
                    deal_slug = re.sub(r"[^\w\-]", "_", listing["deal_name"])
                    date_str = listing["report_date"].replace("-", "")
                    filename = f"bnym_{deal_slug}_{date_str}.pdf"

                    path = self._download_file(listing["report_url"], filename)
                    downloaded.append(path)

                except Exception as e:
                    self.logger.error(
                        f"Failed to download {listing.get('deal_name', '?')}: {e}"
                    )

        except Exception as e:
            self.logger.error(f"BNY Mellon scrape failed: {e}")

        return downloaded

    def parse_listing(self, html: str) -> list[dict]:
        """
        Parse BNY Mellon's deal listing page.

        The GCT portal structure varies - this handles common patterns.
        """
        soup = BeautifulSoup(html, "lxml")
        listings = []

        # Deal cards or table rows
        for card in soup.select(".deal-card, .deal-row, tr.deal"):
            name_el = card.select_one(".deal-name, td:first-child")
            date_el = card.select_one(".report-date, td.date")
            link_el = card.select_one("a[href*='.pdf'], a.report-link")

            if name_el and link_el:
                deal_name = name_el.get_text(strip=True)
                report_date = date_el.get_text(strip=True) if date_el else ""
                report_url = urljoin(self.base_url, link_el["href"])

                listings.append({
                    "deal_name": deal_name,
                    "report_date": report_date,
                    "report_url": report_url,
                    "manager": "",
                })

        # Fallback: find all PDF links
        if not listings:
            for link in soup.find_all("a", href=re.compile(r"\.pdf", re.I)):
                text = link.get_text(strip=True)
                if any(kw in text.upper() for kw in ["CLO", "TRUSTEE", "REPORT"]):
                    listings.append({
                        "deal_name": text,
                        "report_date": "",
                        "report_url": urljoin(self.base_url, link["href"]),
                        "manager": "",
                    })

        return listings
