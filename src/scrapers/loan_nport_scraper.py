"""
Loan-level scraper: NPORT-P filings of public closed-end leveraged-loan funds
managed by large CLO managers' loan desks.

Why these funds: CLO trustee reports (the actual loan tapes) are behind trustee
portals and not on EDGAR; CLO 10-D / ABS-15G filings don't exist for 144A CLOs.
The closest *public, loan-level, period-over-period* view of what a CLO
manager's loan desk is buying, selling and marking is the NPORT-P of the
registered loan funds that the same team runs. Public NPORT-P is quarterly.

Each row = one loan facility (assetCat LON) with par (balance), USD value,
maturity, coupon, default and PIK flags.
"""

import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime

import requests

from src.models.schema import LoanHolding, obligor_key

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "CLO-Research-Tool/1.0 (research@example.com)",
    "Accept-Encoding": "gzip, deflate",
}

LOAN_FUNDS = [
    {"ticker": "BSL", "cik": "1486298", "manager": "Blackstone",
     "name": "Blackstone Senior Floating Rate 2027 Term Fund"},
    {"ticker": "VVR", "cik": "1059386", "manager": "Invesco",
     "name": "Invesco Senior Income Trust"},
    {"ticker": "EFR", "cik": "1258623", "manager": "Eaton Vance / Morgan Stanley IM",
     "name": "Eaton Vance Senior Floating-Rate Trust"},
    {"ticker": "FRA", "cik": "1259708", "manager": "BlackRock",
     "name": "BlackRock Floating Rate Income Strategies Fund"},
    {"ticker": "JFR", "cik": "1276533", "manager": "Nuveen",
     "name": "Nuveen Floating Rate Income Fund"},
    {"ticker": "KIO", "cik": "1515940", "manager": "KKR",
     "name": "KKR Income Opportunities Fund"},
    {"ticker": "ARDC", "cik": "1515324", "manager": "Ares",
     "name": "Ares Dynamic Credit Allocation Fund"},
]
FUND_BY_TICKER = {f["ticker"]: f for f in LOAN_FUNDS}


def _strip_ns(root):
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _d(x):
    try:
        return datetime.strptime(x[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def parse_nport_loans(xml_text: str) -> tuple[date | None, list[dict]]:
    """Parse an NPORT-P primary_doc.xml into (period_end, loan rows)."""
    root = _strip_ns(ET.fromstring(xml_text.encode() if isinstance(xml_text, str) else xml_text))
    rep = root.find(".//genInfo/repPdDate")
    period = _d(rep.text) if rep is not None else None
    rows = []
    for sec in root.iter("invstOrSec"):
        if (sec.findtext("assetCat") or "").strip() != "LON":
            continue
        if (sec.findtext("payoffProfile") or "Long") != "Long":
            continue
        name = (sec.findtext("name") or "").strip()
        if not name:
            continue
        title = (sec.findtext("title") or "").strip()
        cusip = (sec.findtext("cusip") or "").strip()
        if cusip in ("000000000", "N/A", "NA"):
            cusip = ""
        debt = sec.find("debtSec")
        cur = (sec.findtext("curCd") or "").strip()
        fx = 1.0
        cc = sec.find("currencyConditional")
        if cc is not None:
            cur = cc.get("curCd") or cur
            fx = _f(cc.get("exchangeRt")) or None
        loanx = None
        for o in sec.iter("other"):
            if "loanx" in (o.get("otherDesc") or "").lower():
                loanx = o.get("value")
        bal, val = _f(sec.findtext("balance")), _f(sec.findtext("valUSD"))
        price = (val * fx / bal * 100) if (bal and bal > 0 and val is not None and fx) else None
        rows.append({
            "obligor": name,
            "obligor_key": obligor_key(name),
            "title": title,
            "cusip": cusip or None,
            "loanx_id": loanx,
            "facility_key": cusip or loanx or f"{obligor_key(name)}|{title.lower()}",
            "currency": cur or None,
            "balance": bal,
            "value_usd": val,
            "price": price,
            "pct_nav": _f(sec.findtext("pctVal")),
            "fair_value_level": sec.findtext("fairValLevel"),
            "maturity": _d(debt.findtext("maturityDt")) if debt is not None else None,
            "coupon": _f(debt.findtext("annualizedRt")) if debt is not None else None,
            "is_default": int(debt is not None and debt.findtext("isDefault") == "Y"),
            "is_pik": int(debt is not None and debt.findtext("isPaidKind") == "Y"),
        })
    return period, rows


class LoanNPORTScraper:
    def __init__(self, n_filings: int = 6, delay: float = 0.4):
        self.n = n_filings
        self.delay = delay
        self.http = requests.Session()
        self.http.headers.update(HEADERS)

    def _get(self, url, tries=3):
        for i in range(tries):
            try:
                r = self.http.get(url, timeout=60)
                if r.status_code == 200:
                    return r
                logger.warning("HTTP %s %s", r.status_code, url)
            except requests.RequestException as e:
                logger.warning("error %s %s", e, url)
            time.sleep(2 * (i + 1))
        return None

    def filings(self, cik: str, forms=("NPORT-P",), count=None):
        r = self._get(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json")
        if r is None:
            return []
        rec = r.json()["filings"]["recent"]
        out = []
        for i, form in enumerate(rec["form"]):
            if form in forms:
                out.append({"form": form, "accession": rec["accessionNumber"][i],
                            "filing_date": rec["filingDate"][i],
                            "report_date": rec["reportDate"][i],
                            "primary_doc": rec["primaryDocument"][i]})
                if count and len(out) >= count:
                    break
        return out

    def scrape_fund(self, fund: dict) -> list[dict]:
        rows = []
        seen_periods = set()
        for f in self.filings(fund["cik"], count=self.n + 2):
            if len(seen_periods) >= self.n:
                break
            acc = f["accession"].replace("-", "")
            url = f"https://www.sec.gov/Archives/edgar/data/{int(fund['cik'])}/{acc}/primary_doc.xml"
            r = self._get(url)
            time.sleep(self.delay)
            if r is None:
                continue
            try:
                period, loans = parse_nport_loans(r.content)
            except ET.ParseError as e:
                logger.error("parse error %s: %s", url, e)
                continue
            period = period or _d(f["report_date"])
            if period in seen_periods:      # amended re-filing of same period
                continue
            seen_periods.add(period)
            for row in loans:
                row.update(fund=fund["ticker"], manager=fund["manager"], report_date=period,
                           filing_date=_d(f["filing_date"]), source_url=url)
            rows.extend(loans)
            logger.info("%s %s: %d loans", fund["ticker"], period, len(loans))
        return rows

    def run(self, session, funds=None) -> dict:
        counts = {}
        for fund in funds or LOAN_FUNDS:
            rows = self.scrape_fund(fund)
            if not rows:
                logger.error("%s: no rows — keeping existing data", fund["ticker"])
                counts[fund["ticker"]] = 0
                continue
            session.query(LoanHolding).filter(LoanHolding.fund == fund["ticker"]).delete()
            session.bulk_insert_mappings(LoanHolding, rows)
            session.commit()
            counts[fund["ticker"]] = len(rows)
        return counts
