"""
Scraper for SEC EDGAR CLO-related filings.

SEC filings (10-D, ABS-15G) contain structured data about CLO deals.
EDGAR's full-text search API (EFTS) allows searching for CLO-specific filings.

This is often the most reliable public data source since EDGAR has
well-documented APIs and consistent formatting.
"""

import re
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from src.scrapers.base import BaseScraper


# EDGAR rate limit: max 10 requests/second, must include User-Agent with email
EDGAR_BASE = "https://efts.sec.gov/LATEST"
EDGAR_FILINGS = "https://www.sec.gov/cgi-bin/browse-edgar"
EDGAR_FULLTEXT = "https://efts.sec.gov/LATEST/search-index"


class SECEdgarScraper(BaseScraper):
    """Scrape CLO-related filings from SEC EDGAR."""

    def __init__(self, config: dict):
        super().__init__(config)
        self.trustee_config = config["trustees"]["sec_edgar"]
        self.filing_types = self.trustee_config["filing_types"]

        # EDGAR requires a descriptive User-Agent with contact email
        self.session.headers.update({
            "User-Agent": "CLO-Research-Scraper/1.0 (research@example.com)",
            "Accept-Encoding": "gzip, deflate",
        })

    def scrape(self, limit: int = None) -> list[Path]:
        """
        Search EDGAR for CLO-related filings and download them.

        Uses the EDGAR full-text search API to find filings mentioning
        CLO-related terms, then downloads the actual filing documents.
        """
        downloaded = []
        self.logger.info("Starting SEC EDGAR scrape...")

        for filing_type in self.filing_types:
            try:
                filings = self._search_filings(filing_type, limit=limit)
                self.logger.info(
                    f"Found {len(filings)} {filing_type} filings"
                )

                for filing in filings:
                    try:
                        filename = self._filing_filename(filing)
                        path = self._download_file(filing["document_url"], filename)
                        downloaded.append(path)
                    except Exception as e:
                        self.logger.error(f"Download failed: {e}")

            except Exception as e:
                self.logger.error(f"Search failed for {filing_type}: {e}")

        return downloaded

    def _search_filings(self, filing_type: str, limit: int = None) -> list[dict]:
        """
        Search EDGAR full-text search for CLO filings.

        Uses the EFTS API: https://efts.sec.gov/LATEST/search-index
        """
        filings = []

        # Search for CLO-related 10-D filings
        params = {
            "q": '"collateralized loan obligation" OR "CLO" AND "trustee report"',
            "dateRange": "custom",
            "startdt": (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d"),
            "enddt": datetime.now().strftime("%Y-%m-%d"),
            "forms": filing_type,
        }

        try:
            resp = self._request(
                f"{EDGAR_BASE}/search-index",
                params=params
            )
            data = resp.json()

            for hit in data.get("hits", {}).get("hits", []):
                source = hit.get("_source", {})
                filing_info = {
                    "deal_name": source.get("display_names", [""])[0],
                    "filing_type": filing_type,
                    "filed_date": source.get("file_date", ""),
                    "accession_number": source.get("accession_no", ""),
                    "document_url": self._build_filing_url(source),
                    "cik": source.get("ciks", [""])[0] if source.get("ciks") else "",
                    "entity_name": source.get("entity_name", ""),
                }
                filings.append(filing_info)

                if limit and len(filings) >= limit:
                    break

        except Exception as e:
            self.logger.error(f"EFTS search error: {e}")

            # Fallback: use the classic EDGAR full-text search
            filings = self._search_classic(filing_type, limit)

        return filings

    def _search_classic(self, filing_type: str, limit: int = None) -> list[dict]:
        """Fallback: search using classic EDGAR browse interface."""
        filings = []

        params = {
            "action": "getcompany",
            "type": filing_type,
            "dateb": "",
            "owner": "include",
            "count": min(limit or 40, 40),
            "search_text": "CLO",
            "action": "getcompany",
            "output": "atom",
        }

        try:
            resp = self._request(EDGAR_FILINGS, params=params)
            soup = BeautifulSoup(resp.text, "lxml-xml")

            for entry in soup.find_all("entry"):
                title = entry.find("title")
                link = entry.find("link")
                updated = entry.find("updated")

                if title and link:
                    filings.append({
                        "deal_name": title.get_text(strip=True),
                        "filing_type": filing_type,
                        "filed_date": updated.get_text(strip=True)[:10] if updated else "",
                        "document_url": link.get("href", ""),
                        "entity_name": "",
                    })

        except Exception as e:
            self.logger.error(f"Classic EDGAR search error: {e}")

        return filings

    def _build_filing_url(self, source: dict) -> str:
        """Build the URL to the actual filing document from EFTS metadata."""
        accession = source.get("accession_no", "").replace("-", "")
        file_name = source.get("file_name", "")

        if accession and file_name:
            return (
                f"https://www.sec.gov/Archives/edgar/data/"
                f"{source.get('ciks', [''])[0]}/{accession}/{file_name}"
            )
        return ""

    def _filing_filename(self, filing: dict) -> str:
        """Generate a clean local filename for a filing."""
        name = re.sub(r"[^\w\-]", "_", filing.get("deal_name", "unknown"))
        date = filing.get("filed_date", "").replace("-", "")
        ftype = filing.get("filing_type", "").replace("-", "")
        return f"edgar_{ftype}_{name}_{date}.pdf"

    def parse_listing(self, html: str) -> list[dict]:
        """Parse EDGAR search results page (used for classic HTML results)."""
        soup = BeautifulSoup(html, "lxml")
        listings = []

        for row in soup.select("table.tableFile2 tr"):
            cells = row.find_all("td")
            if len(cells) < 4:
                continue

            filing_type = cells[0].get_text(strip=True)
            if filing_type not in self.filing_types:
                continue

            link = cells[1].find("a", href=True)
            if link:
                listings.append({
                    "deal_name": cells[1].get_text(strip=True),
                    "report_date": cells[3].get_text(strip=True),
                    "report_url": urljoin("https://www.sec.gov", link["href"]),
                    "filing_type": filing_type,
                })

        return listings
