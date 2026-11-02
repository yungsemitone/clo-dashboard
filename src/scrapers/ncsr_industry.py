"""
Industry classification from each loan fund's own N-CSR / N-CSRS schedule of
investments. Schedules group positions under industry headings such as
"Aerospace and Defense — 2.8%"; every borrower listed under a heading inherits
that (fund-reported) industry. Headings vary by fund (GICS-ish for Eaton Vance /
BlackRock, Moody's-ish for others) so they are also mapped to a common macro
sector bucket via `normalize_sector`.
"""

import logging
import re

import warnings

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

from src.models.schema import ObligorIndustry, obligor_key

logger = logging.getLogger(__name__)

HEADING_RE = re.compile(
    r"^([A-Z][A-Za-z0-9&,'’\-/\.\(\) ]{2,90}?)\s*(?:[—–:]|-{1,2})?\s*\(?-?\d{1,3}(?:\.\d+)?\s*%\)?\*?"
    r"(?:\s*\(\d{1,3}(?:\.\d+)?% of Total Investments\))?$")
SKIP_HEADINGS = re.compile(
    r"total|loans|bonds|notes|securities|stocks|equit|warrants|investments|interests|"
    r"collateralized|asset-backed|short-term|money market|net assets|liabilities|"
    r"common|preferred|rights|units|cash|options|swaps|portfolio|purchased|other assets|"
    r"average|yield|leverage|investment compan|exchange-traded|specialized finance|structured|"
    r"nav|market|expense|coverage|return|distribution|rate",
    re.I)

# keyword -> macro sector bucket (first match wins; order matters)
SECTOR_RULES = [
    (r"software|internet|it services|information tech|technology|computer|semiconductor|data process|electronic", "Technology & Software"),
    (r"health|pharma|biotech|life science|medical|hospital", "Healthcare"),
    (r"telecom|wireless|cable|satellite|communications? equip|diversified telecommunication", "Telecom & Cable"),
    (r"media|broadcast|entertainment|publish|advertis|interactive|gaming|casino", "Media & Entertainment"),
    (r"hotel|restaurant|leisure|lodging|travel|airline|cruise", "Leisure, Travel & Restaurants"),
    (r"retail|apparel|textile|luxury|consumer|household|personal|beverage|food|tobacco|durable|home|leisure products|specialty retail|distributor", "Consumer"),
    (r"oil|gas|energy|coal|petrol|pipeline", "Energy"),
    (r"utilit|electric|power|independent power|renewable", "Utilities & Power"),
    (r"bank|insurance|financ|capital markets|brokerage|asset management|mortgage|reit|real estate", "Financials & Insurance"),
    (r"chemical|metal|mining|steel|paper|forest|packaging|container|glass|construction materials|building products", "Chemicals, Materials & Packaging"),
    (r"aerospace|defense", "Aerospace & Defense"),
    (r"auto|transport|trucking|logistic|air freight|marine|road|rail", "Autos & Transportation"),
    (r"construct|engineering|machinery|industrial|electrical equip|trading compan|conglomerate|manufactur|capital equipment", "Industrials & Capital Goods"),
    (r"commercial services|professional services|business services|services|staffing|environmental|education|diversified consumer services", "Business & Professional Services"),
]


def normalize_sector(industry: str) -> str:
    s = (industry or "").lower()
    for pat, sector in SECTOR_RULES:
        if re.search(pat, s):
            return sector
    return "Other"


def _rows_text(html: str):
    soup = BeautifulSoup(html, "lxml")
    for tr in soup.find_all("tr"):
        cells = [re.sub(r"[\s\xa0]+", " ", td.get_text(" ")).strip() for td in tr.find_all(["td", "th"])]
        cells = [c for c in cells if c and c not in ("$",)]
        if cells:
            yield cells


def parse_ncsr_industries(html: str) -> dict[str, str]:
    """Return {obligor_key: industry heading} for every borrower under a heading."""
    out: dict[str, str] = {}
    current = None
    for cells in _rows_text(html):
        joined = " ".join(cells)
        first = cells[0]
        m = HEADING_RE.match(joined) or HEADING_RE.match(first)
        if m and len(joined) < 110:
            name = re.sub(r"\s*\([a-z0-9]{1,2}\)$", "", m.group(1)).strip(" ,-")
            if name.isupper():
                name = name.title().replace(" And ", " and ")
            dashed = re.search(r"[—–]|\s-\s", joined) is not None
            if SKIP_HEADINGS.search(name) or (not dashed and normalize_sector(name) == "Other"):
                current = None
            else:
                current = name
            continue
        # heading rendered as a lone text cell with no numbers (some funds put % in next cell)
        if (len(cells) <= 2 and re.fullmatch(r"[A-Z][A-Za-z&,'’\-/ ]{3,60}", first)
                and (len(cells) == 1 or re.fullmatch(r"\(?\d{1,3}\.\d+\s*%\)?", cells[1]))
                and not SKIP_HEADINGS.search(first) and first.istitle()):
            if len(cells) == 2:
                current = first
                continue
        if current is None:
            continue
        if re.match(r"^total", first, re.I):
            continue
        # borrower name = text up to first comma / colon / " Term" / " Incremental"
        if not re.search(r"\d", joined):
            continue
        cand = [c for c in cells if re.match(r"[A-Za-z]", c) and (("," in c) or c.endswith(":") or "Term Loan" in c)]
        if not cand:
            continue
        nm = re.split(r",|:| Term Loan| Incremental| Delayed| Revolv| First Lien| Second Lien| 1st Lien| 2nd Lien| \d{4} ", cand[0])[0].strip()
        if len(nm) < 3 or nm.lower().startswith(("term", "series", "class", "incremental", "delayed", "revolv", "first", "second", "tranche")):
            continue
        k = obligor_key(nm)
        if k and k not in out:
            out[k] = current
    return out


def scrape_industries(scraper, session, funds) -> dict:
    """For each fund, parse the latest N-CSR/N-CSRS and upsert obligor industries."""
    counts = {}
    for fund in funds:
        fl = scraper.filings(fund["cik"], forms=("N-CSR", "N-CSRS"), count=1)
        if not fl:
            counts[fund["ticker"]] = 0
            continue
        f = fl[0]
        acc = f["accession"].replace("-", "")
        url = f"https://www.sec.gov/Archives/edgar/data/{int(fund['cik'])}/{acc}/{f['primary_doc']}"
        r = scraper._get(url)
        if r is None:
            counts[fund["ticker"]] = 0
            continue
        mapping = parse_ncsr_industries(r.text)
        if not mapping:
            counts[fund["ticker"]] = 0
            continue
        session.query(ObligorIndustry).filter(ObligorIndustry.source_fund == fund["ticker"]).delete()
        session.bulk_insert_mappings(ObligorIndustry, [
            {"obligor_key": k, "industry_raw": v, "sector": normalize_sector(v),
             "source_fund": fund["ticker"], "source_url": url} for k, v in mapping.items()])
        session.commit()
        counts[fund["ticker"]] = len(mapping)
    return counts
