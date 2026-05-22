"""
Scraper for US Bank's CLO trustee reporting portal.

US Bank (via their Pivot platform) hosts trustee reports for a large
number of CLO deals. Reports are typically PDFs containing OC/IC test
results, collateral quality metrics, and waterfall distributions.

NOTE: You may need to adjust selectors and URLs as the portal structure
can change. Consider using Selenium for JavaScript-rendered pages.
"""

import re
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from src.scrapers.base import BaseScraper


class USBankScraper(BaseScraper):
    """Scrape CLO trustee reports from US Bank's Pivot platform."""

    def __init__(self, config: dict):
        super().__init__(config)
        self.trustee_config = config["trustees"]["us_bank"]
        self.base_url = self.trustee_config["base_url"]

    def scrape(self, limit: int = None) -> list[Path]:
        """
        Scrape US Bank trustee reports.

        Strategy:
        1. Hit the report index page
        2. Extract deal listing with report links
        3. Download each report PDF

        Returns list of paths to downloaded PDFs.
        """
        downloaded = []
        self.logger.info("Starting US Bank scrape...")

        try:
            # Step 1: Get the report index
            # The Pivot platform may require session setup or auth tokens
            # for public reports. Adjust as needed.
            index_url = self.trustee_config["report_index_url"]
            resp = self._request(index_url)
            listings = self.parse_listing(resp.text)

            if limit:
                listings = listings[:limit]

            self.logger.info(f"Found {len(listings)} report listings")

            # Step 2: Download each report
            for listing in listings:
                try:
                    # Build a clean filename
                    deal_slug = re.sub(r"[^\w\-]", "_", listing["deal_name"])
                    date_str = listing["report_date"].replace("-", "")
                    filename = f"usbank_{deal_slug}_{date_str}.pdf"

                    path = self._download_file(listing["report_url"], filename)
                    downloaded.append(path)

                except Exception as e:
                    self.logger.error(
                        f"Failed to download {listing.get('deal_name', '?')}: {e}"
                    )

        except Exception as e:
            self.logger.error(f"US Bank scrape failed: {e}")

        return downloaded

    def parse_listing(self, html: str) -> list[dict]:
        """
        Parse the US Bank report index page.

        Looks for report links matching CLO deal patterns.
        Returns list of {deal_name, report_date, report_url, manager}.

        NOTE: These selectors are approximations - you'll need to inspect
        the actual Pivot portal HTML and adjust accordingly.
        """
        soup = BeautifulSoup(html, "lxml")
        listings = []

        # Look for table rows or list items containing report links
        # Common patterns on trustee portals:
        #   - Table with columns: Deal Name | Report Date | Download
        #   - Accordion sections per deal with report links

        # Pattern 1: Table-based listing
        for row in soup.select("table.report-list tr, table.deals tr"):
            cells = row.find_all("td")
            if len(cells) < 3:
                continue

            deal_name = cells[0].get_text(strip=True)
            report_date = cells[1].get_text(strip=True)
            link_tag = cells[2].find("a", href=True)

            if link_tag and self._is_clo_deal(deal_name):
                listings.append({
                    "deal_name": deal_name,
                    "report_date": self._normalize_date(report_date),
                    "report_url": urljoin(self.base_url, link_tag["href"]),
                    "manager": cells[3].get_text(strip=True) if len(cells) > 3 else "",
                })

        # Pattern 2: Link-based listing (PDF links with CLO-like names)
        if not listings:
            for link in soup.find_all("a", href=re.compile(r"\.pdf", re.I)):
                href = link["href"]
                text = link.get_text(strip=True)

                if self._is_clo_deal(text):
                    date_match = re.search(r"(\d{4}[-/]\d{2}[-/]\d{2})", text + href)
                    listings.append({
                        "deal_name": text,
                        "report_date": date_match.group(1) if date_match else "",
                        "report_url": urljoin(self.base_url, href),
                        "manager": "",
                    })

        return listings

    @staticmethod
    def _is_clo_deal(name: str) -> bool:
        """Heuristic: does this look like a CLO deal name?"""
        clo_patterns = [
            r"\bCLO\b",
            r"\bCDO\b",
            r"\bFunding\b.*\b(I{1,4}|IV|V|VI|VII|VIII|IX|X|\d+)\b",
            r"\bLoan\s+Fund",
            r"\bCredit\s+Fund",
            r"\bLeverage[d]?\s+Loan",
        ]
        return any(re.search(p, name, re.I) for p in clo_patterns)

    @staticmethod
    def _normalize_date(date_str: str) -> str:
        """Try to normalize various date formats to YYYY-MM-DD."""
        import dateutil.parser
        try:
            return dateutil.parser.parse(date_str).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            return date_str
