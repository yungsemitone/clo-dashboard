"""
Abstract base class for trustee portal scrapers.
"""

import time
import logging
from abc import ABC, abstractmethod
from pathlib import Path

import requests


class BaseScraper(ABC):
    """
    Base class for all trustee scrapers.

    Subclasses implement:
      - scrape(): crawl the portal and download reports
      - parse_listing(): extract report URLs from an index page
    """

    def __init__(self, config: dict):
        self.config = config
        self.scraper_config = config["scraper"]
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": self.scraper_config["user_agent"],
        })
        self.raw_dir = Path(self.scraper_config["raw_dir"])
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger(self.__class__.__name__)

    def _request(self, url: str, method: str = "GET", **kwargs) -> requests.Response:
        """Make an HTTP request with retry logic and rate limiting."""
        retries = self.scraper_config["max_retries"]
        timeout = self.scraper_config["timeout"]

        for attempt in range(1, retries + 1):
            try:
                resp = self.session.request(
                    method, url, timeout=timeout, **kwargs
                )
                resp.raise_for_status()

                # Rate limiting
                time.sleep(self.scraper_config["request_delay"])
                return resp

            except requests.exceptions.RequestException as e:
                self.logger.warning(f"Request failed (attempt {attempt}/{retries}): {e}")
                if attempt == retries:
                    raise
                time.sleep(self.scraper_config["request_delay"] * attempt)

    def _download_file(self, url: str, filename: str) -> Path:
        """Download a file to the raw data directory."""
        filepath = self.raw_dir / filename

        if filepath.exists():
            self.logger.info(f"Already downloaded: {filename}")
            return filepath

        resp = self._request(url)
        filepath.write_bytes(resp.content)
        self.logger.info(f"Downloaded: {filename} ({len(resp.content)} bytes)")
        return filepath

    @abstractmethod
    def scrape(self, limit: int = None) -> list[Path]:
        """
        Scrape the trustee portal.

        Returns a list of Paths to downloaded report files.
        """
        pass

    @abstractmethod
    def parse_listing(self, html: str) -> list[dict]:
        """
        Parse an index/listing page to extract report metadata.

        Returns list of dicts with keys like:
          - deal_name
          - report_date
          - report_url
          - manager (if available)
        """
        pass
