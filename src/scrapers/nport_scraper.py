"""
Scraper for NPORT-P filings from public CLO equity funds.
Pulls real deal names, managers, par values, and market values.
"""

import re
import logging
import time
from pathlib import Path
from datetime import date

import requests
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from src.models.schema import Deal, FundHolding

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "CLO-Research-Tool/1.0 (research@example.com)",
    "Accept-Encoding": "gzip, deflate",
}

CLO_FUNDS = [
    {"name": "Oxford Lane Capital", "ticker": "OXLC", "cik": "0001495222"},
    {"name": "Eagle Point Credit", "ticker": "ECC", "cik": "0001604174"},
    {"name": "OFS Credit Company", "ticker": "OCCI", "cik": "0001716951"},
    {"name": "Pearl Diver Credit", "ticker": "PDCC", "cik": "0001998043"},
    {"name": "Priority Income Fund", "ticker": "PRIF", "cik": "0001554625"},
]

# SPV platform name -> actual CLO manager
# Each key is a lowercase prefix that appears in the NPORT issuer "name" field.
# The scraper checks these in order; first match wins.
MANAGER_MAP = {
    # --- Top 30 global CLO managers ---
    "blackstone": "Blackstone",
    "gso capital": "Blackstone",
    "carlyle": "Carlyle Group",
    "apollo": "Apollo Global Management",
    "ares": "Ares Management",
    "hps": "HPS Investment Partners",
    "blackrock": "BlackRock",
    "magnetite": "BlackRock",
    "pgim": "PGIM Fixed Income",
    "dryden": "PGIM Fixed Income",
    "cifc": "CIFC Asset Management",
    "credit suisse": "Credit Suisse Asset Mgmt",
    "madison park": "Credit Suisse Asset Mgmt",
    "oak hill": "Oak Hill Advisors",
    "oha": "Oak Hill Advisors",
    "oaktree": "Oaktree Capital",
    "onex": "Onex Credit Partners",
    "ocp clo": "Onex Credit Partners",
    "octagon": "Octagon Credit Investors",
    "sound point": "Sound Point Capital",
    "neuberger berman": "Neuberger Berman",
    "goldentree": "GoldenTree Asset Management",
    "goldentr": "GoldenTree Asset Management",
    "kkr": "KKR Credit Advisors",
    "bain capital": "Bain Capital Credit",
    "blue owl": "Blue Owl Capital",
    "owl rock": "Blue Owl Capital",
    "golub capital": "Golub Capital",
    "palmer square": "Palmer Square Capital",
    "nuveen": "Nuveen (TIAA)",
    "symphony clo": "Nuveen (TIAA)",
    "pinebridge": "PineBridge Investments",
    "invesco": "Invesco",

    # --- Major CLO platforms and their managers ---
    "bluemountain": "BlueMountain Capital",
    "sculptor": "Sculptor Capital",
    "halcyon": "Sculptor Capital",
    "oz management": "Sculptor Capital",
    "battalion": "Brigade Capital",
    "brigade": "Brigade Capital",
    "barings": "Barings",
    "benefit street": "Benefit Street Partners",
    "rockford tower": "Benefit Street Partners",
    "cedar funding": "Nomura",
    "nomura": "Nomura",
    "venture cdo": "MJX Asset Management",
    "venture clo": "MJX Asset Management",
    "mjx": "MJX Asset Management",
    "columbia threadneedle": "Columbia Threadneedle",
    "cent clo": "Columbia Threadneedle",
    "wind river": "Napier Park Global Capital",
    "napier park": "Napier Park Global Capital",
    "first eagle": "Napier Park Global Capital",
    "regatta": "Napier Park Global Capital",
    "cvc credit": "CVC Credit Partners",
    "apidos": "CVC Credit Partners",
    "cordatus": "CVC Credit Partners",
    "tcw": "TCW Group",
    "generate clo": "Generate Advisors",
    "generate advisors": "Generate Advisors",
    "elmwood": "Elmwood Asset Management",
    "signal peak": "Signal Peak Capital",
    "cbam": "CBAM Partners",
    "crestline": "Crestline Management",
    "elevation clo": "Elevation CLO Management",
    "canyon": "Canyon Partners",
    "canyon clo": "Canyon Partners",
    "kayne": "Kayne Anderson",
    "nassau": "Nassau Corporate Credit",
    "nassau corporate": "Nassau Corporate Credit",
    "whitehorse": "WhiteHorse Capital",
    "alcentra": "BNY Mellon / Alcentra",
    "anchorage": "Anchorage Capital",
    "aimco": "Allstate / AIMCO",
    "allstate": "Allstate / AIMCO",
    "antares": "Antares Capital",
    "arrowmark": "ArrowMark Partners",
    "atrium": "CIFC Asset Management",
    "babson": "Barings",
    "ballyrock": "Ballyrock Investment Advisors",
    "wellfleet": "Wellfleet Credit Partners",
    "marble point": "Marble Point Credit Management",
    "greywolf": "Greywolf Capital",
    "hayfin": "Hayfin Capital Management",
    "harvest clo": "Harvest CLO",
    "haymaker": "GSO / Blackstone",
    "jersey street": "Ellington Management",
    "limerock": "Limerock CLO",
    "man glo": "Man GLG",
    "man glg": "Man GLG",
    "midocean": "MidOcean Credit Partners",
    "monroe": "Monroe Capital",
    "morgan stanley": "Morgan Stanley",
    "mufg": "MUFG",
    "oc iii": "Owl Rock / Blue Owl",
    "och-ziff": "Sculptor Capital",
    "osprey": "Osprey Capital",
    "owl rock clo": "Blue Owl Capital",
    "pan american": "Pan American Finance",
    "primus": "Primus CLO",
    "recette": "Recette CLO",
    "redding ridge": "Redding Ridge Asset Management",
    "romark": "Romark Credit Advisors",
    "sixth street": "Sixth Street Partners",
    "steele creek": "Steele Creek Investment Mgmt",
    "trinitas": "Trinitas Capital Management",
    "voya": "Voya Investment Management",
    "voya clo": "Voya Investment Management",
    "wells fargo": "Wells Fargo",
    "zais": "ZAIS Group",
    "york clo": "York Capital",

    # Eagle Point proprietary CLOs (all "Park" names + related)
    "basswood park": "Eagle Point Credit",
    "bear mountain park": "Eagle Point Credit",
    "belmont park": "Eagle Point Credit",
    "bethpage park": "Eagle Point Credit",
    "bristol park": "Eagle Point Credit",
    "kings park": "Eagle Point Credit",
    "whetstone park": "Eagle Point Credit",
    "wellman park": "Eagle Point Credit",
    "harvest park": "Eagle Point Credit",
    "catskill park": "Eagle Point Credit",
    "clonkeen park": "Eagle Point Credit",
    "danby park": "Eagle Point Credit",
    "dewolf park": "Eagle Point Credit",
    "franklin park": "Eagle Point Credit",
    "lake george park": "Eagle Point Credit",
    "lodi park": "Eagle Point Credit",
    "meacham park": "Eagle Point Credit",
    "milford park": "Eagle Point Credit",
    "niagra park": "Eagle Point Credit",
    "nyack park": "Eagle Point Credit",
    "peace park": "Eagle Point Credit",
    "pixley park": "Eagle Point Credit",
    "point au roche": "Eagle Point Credit",
    "rockland park": "Eagle Point Credit",
    "thompson park": "Eagle Point Credit",
    "unity-peace park": "Eagle Point Credit",
    "wehle park": "Eagle Point Credit",
    "wild park": "Eagle Point Credit",
    "wildpk": "Eagle Point Credit",
    "wpark": "Eagle Point Credit",
    "myers park": "Blackstone",

    # Other common platforms
    "alm loan": "Apollo Global Management",
    "alm ": "Apollo Global Management",
    "alf ": "Apollo Global Management",
    "allegro clo": "AXA Investment Managers",
    "alleg": "AXA Investment Managers",
    "axa": "AXA Investment Managers",
    "eaton vance": "Eaton Vance / Morgan Stanley",
    "eaton": "Eaton Vance / Morgan Stanley",
    "guggenheim": "Guggenheim Partners",
    "ing ": "ING Capital",
    "jpmorgan": "JPMorgan",
    "lakeside park": "Lakeside Park CLO Mgmt",
    "lmf": "LibreMax Capital",
    "libremax": "LibreMax Capital",
    "new mountain": "New Mountain Capital",
    "new york life": "NYL Investors",
    "nyl": "NYL Investors",
    "obra": "Obra Capital",
    "eldridge": "Eldridge Capital Management",
    "tikehau": "Tikehau Capital",
    "thl credit": "THL Credit",
    "t. rowe": "T. Rowe Price",
    "t rowe": "T. Rowe Price",
    "galaxy": "PineBridge Investments",
    "ammc": "American Money Management",
    "american money": "American Money Management",
    "acis clo": "DWS Group",
    "avery point": "Bain Capital Credit",
    "flatiron clo": "NYL Investors",
    "race point": "Bain Capital Credit",
    "recette": "Recette CLO Management",
    "castle lake": "Castle Lake Capital",
    "green harbour": "Green Harbour Capital",

    # CUSIP-style abbreviations (from NPORT title fields used as names)
    "anchc": "Anchorage Capital",
    "apexc": "Apex Credit Partners",
    "apex credit": "Apex Credit Partners",
    "atclo": "Ares Management",
    "awpt": "Elevation CLO Management",
    "babsn": "Barings",
    "bally": "Ballyrock Investment Advisors",
    "batln": "Brigade Capital",
    "bluem": "BlueMountain Capital",
    "brdgs": "Bridge Street Capital",
    "bwcap": "Blackstone",
    "canyc": "Canyon Partners",
    "cgms": "Carlyle Group",
    "crowpt": "Crown Point Capital",
    "crown point": "Crown Point Capital",
    "drslf": "PGIM Fixed Income",
    "empwr": "Empower CLO Management",
    "empower clo": "Empower CLO Management",
    "gall": "Gallatin CLO Management",
    "gnrt": "Generate Advisors",
    "harv": "Harvest CLO Management",
    "icg ": "Intermediate Capital Group",
    "icg us": "Intermediate Capital Group",
    "invco": "Invesco",
    "jtwn": "Jamestown CLO Management",
    "kllm": "KKR Credit Advisors",
    "mido": "MidOcean Credit Partners",
    "midocc": "MidOcean Credit Partners",
    "mp clo": "Credit Suisse Asset Mgmt",
    "mp20": "Credit Suisse Asset Mgmt",
    "mp23": "Credit Suisse Asset Mgmt",
    "nmc ": "New Mountain Capital",
    "ofsi": "OFS Capital Management",
    "pkblu": "Park Blue CLO Management",
    "park blue": "Park Blue CLO Management",
    "ppmc": "Palmer Square Capital",
    "reg19": "Napier Park Global Capital",
    "rockt": "Benefit Street Partners",
    "rr ": "Redding Ridge Asset Management",
    "sndpt": "Sound Point Capital",
    "speak": "Signal Peak Capital",
    "stcr": "Steele Creek Investment Mgmt",
    "trnts": "Trinitas Capital Management",
    "vibr": "Vibrant CLO Management",
    "vibrant": "Vibrant CLO Management",
    "welf": "Wellfleet Credit Partners",
    "zclo": "ZAIS Group",

    # Deal platforms used as manager names
    "venture": "MJX Asset Management",
    "marathon clo": "Marathon Asset Management",
    "marathon ": "Marathon Asset Management",
    "shackleton": "Alcentra / BNY Mellon",
    "telos clo": "Telos CLO Management",
    "telos ": "Telos CLO Management",
    "tralee": "Tralee CLO Management",
    "lcm ": "LCM Partners",
    "1988 clo": "1988 Capital Management",
    "37 capital": "37 Capital CLO Management",
    "agl clo": "AGL Credit Management",
    "agl ": "AGL Credit Management",
    "aig clo": "AIG",
    "atlas senior": "Crescent Capital",
    "aqueduct": "Aqueduct Capital Group",
    "aurium": "Spire Partners",
    "avoca": "KKR Credit Advisors",
    "bardin hill": "Bardin Hill Investment Group",
    "brant point": "Brant Point CLO Management",
    "bridge street": "Bridge Street Capital",
    "california street": "Blue Owl Capital",
    "carval": "CarVal Investors",
    "cathedral lake": "Cathedral Lake CLO Management",
    "churchill": "Churchill Asset Management",
    "cqs": "CQS Investment Management",
    "cutwater": "Cutwater Asset Management",
    "fortress": "Fortress Investment Group",
    "harbourview": "HarbourView CLO Management",
    "henley": "Henley CLO Management",
    "jefferson mill": "Jefferson Mill CLO Management",
    "kennedy lewis": "Kennedy Lewis Investment Management",
    "lake shore": "Lake Shore CLO Management",
    "mountain view": "Seix Investment Advisors",
    "muzinich": "Muzinich & Co",
    "ocp euro": "Onex Credit Partners",
    "polus": "Polus Capital Management",
    "post clo": "Post Advisory Group",
    "rad clo": "Rad CLO Management",
    "rad ": "Rad CLO Management",
    "riserva": "Riserva CLO Management",
    "bbam": "BBAM Partners",

    # Generic catch-alls
    "bnp paribas": "BNP Paribas",
    "deutsche bank": "Deutsche Bank",
    "morgan stanley": "Morgan Stanley",
    "wells fargo": "Wells Fargo",
}


class NPORTScraper:
    def __init__(self, config: dict):
        self.config = config
        self.raw_dir = Path(config["scraper"]["raw_dir"])
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def scrape(self, limit: int = None) -> list[dict]:
        all_holdings = []
        funds = CLO_FUNDS[:limit] if limit else CLO_FUNDS

        for fund in funds:
            logger.info(f"Processing {fund['name']}...")
            try:
                filings = self._get_filings(fund["cik"], "NPORT-P", count=6)
                if not filings:
                    logger.warning(f"No NPORT-P filings for {fund['name']}")
                    continue

                for filing in filings[:4]:  # Last 4 quarterly filings
                    raw_xml = self._download_raw_xml(fund, filing)
                    if raw_xml:
                        holdings = self._parse_xml(raw_xml, fund, filing)
                        all_holdings.extend(holdings)
                        logger.info(f"  {fund['name']}: {len(holdings)} CLO holdings")

            except Exception as e:
                logger.error(f"Failed processing {fund['name']}: {e}")
            time.sleep(1)

        logger.info(f"Total CLO holdings scraped: {len(all_holdings)}")
        return all_holdings

    def store(self, holdings: list[dict], db_session: Session):
        deals_created = 0
        holdings_created = 0

        for h in holdings:
            deal_name = h["deal_name"]
            manager = h["manager"]

            deal = db_session.query(Deal).filter_by(deal_name=deal_name).first()
            if not deal:
                deal = Deal(
                    deal_name=deal_name,
                    manager=manager,
                    status="active",
                    source_url=h.get("source_url", ""),
                )
                db_session.add(deal)
                db_session.flush()
                deals_created += 1

            from datetime import datetime as dt
            filing_date = dt.strptime(h["filing_date"], "%Y-%m-%d").date() if isinstance(h["filing_date"], str) else h["filing_date"]

            existing = db_session.query(FundHolding).filter_by(
                deal_id=deal.id, source_fund=h["source_fund"], filing_date=filing_date,
            ).first()

            if not existing:
                holding = FundHolding(
                    deal_id=deal.id, source_fund=h["source_fund"], filing_date=filing_date,
                    par_amount=h.get("par_amount"), market_value=h.get("market_value"),
                    cusip=h.get("cusip", ""),
                )
                db_session.add(holding)
                holdings_created += 1

        db_session.commit()
        logger.info(f"Deals created: {deals_created}, Holdings stored: {holdings_created}")
        return deals_created, holdings_created

    def _get_filings(self, cik, form_type, count=3):
        url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        try:
            resp = self.session.get(url, timeout=30)
            if resp.status_code != 200:
                return []
            data = resp.json()
            recent = data.get("filings", {}).get("recent", {})
            forms = recent.get("form", [])
            dates = recent.get("filingDate", [])
            accessions = recent.get("accessionNumber", [])
            primary_docs = recent.get("primaryDocument", [])

            filings = []
            for i, form in enumerate(forms):
                if form == form_type:
                    filings.append({
                        "date": dates[i], "accession": accessions[i],
                        "primary_doc": primary_docs[i],
                    })
                    if len(filings) >= count:
                        break
            return filings
        except Exception as e:
            logger.error(f"Submissions API error: {e}")
            return []

    def _download_raw_xml(self, fund, filing):
        cik = fund["cik"]
        acc_clean = filing["accession"].replace("-", "")
        index_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_clean}/{filing['accession']}-index.htm"

        try:
            resp = self.session.get(index_url, timeout=30)
            if resp.status_code != 200:
                return None

            soup = BeautifulSoup(resp.text, "lxml")
            xml_href = None
            for row in soup.select("table.tableFile tr"):
                cells = row.find_all("td")
                if len(cells) >= 3:
                    doc_name = cells[2].get_text(strip=True)
                    link = cells[2].find("a", href=True)
                    if doc_name == "primary_doc.xml" and link:
                        xml_href = link["href"]
                        break

            if not xml_href:
                return None

            xml_url = f"https://www.sec.gov{xml_href}"
            resp = self.session.get(xml_url, timeout=60)
            if resp.status_code == 200:
                filename = f"nport_{fund['ticker']}_{filing['date']}.xml"
                filepath = self.raw_dir / filename
                filepath.write_bytes(resp.content)
                return filepath
        except Exception as e:
            logger.error(f"Download error: {e}")
        return None

    def _parse_xml(self, filepath, fund, filing):
        content = filepath.read_text(errors="replace")

        names = re.findall(r'<(?:[\w]+:)?name>([^<]+)</(?:[\w]+:)?name>', content)
        titles = re.findall(r'<(?:[\w]+:)?title>([^<]+)</(?:[\w]+:)?title>', content)
        balances = re.findall(r'<(?:[\w]+:)?balance>([^<]+)</(?:[\w]+:)?balance>', content)
        values = re.findall(r'<(?:[\w]+:)?valUSD>([^<]+)</(?:[\w]+:)?valUSD>', content)
        cusips = re.findall(r'<(?:[\w]+:)?cusip>([^<]+)</(?:[\w]+:)?cusip>', content)

        holdings = []
        for i in range(len(names)):
            name = names[i].strip()
            title = titles[i].strip() if i < len(titles) else ""
            full_name = f"{name} {title}".lower()

            is_clo = any(kw in full_name for kw in [
                "clo", "loan fund", "credit fund", "funding ltd",
                "credit opportunities", "loan trust", "senior loan",
            ])
            if not is_clo:
                continue

            try:
                par = float(balances[i]) if i < len(balances) else 0
            except ValueError:
                par = 0
            try:
                val = float(values[i]) if i < len(values) else 0
            except ValueError:
                val = 0

            deal_name = self._clean_deal_name(name, title)
            manager = self._normalize_manager(name)

            holdings.append({
                "deal_name": deal_name,
                "manager": manager,
                "par_amount": par,
                "market_value": val,
                "cusip": cusips[i].strip() if i < len(cusips) else "",
                "source_fund": fund["ticker"],
                "filing_date": filing["date"],
                "source_url": f"EDGAR NPORT-P: {fund['name']} ({filing['date']})",
            })

        return holdings

    @staticmethod
    def _normalize_manager(raw_name: str) -> str:
        """Map SPV/issuer name to the actual CLO management firm."""
        # Clean the raw name
        name = raw_name.strip()
        for suffix in ["Ltd", "Ltd.", "LLC", "LP", "Inc", "Inc.",
                       "LTD", "L.P.", "L.L.C.", ", Ltd", ", LLC"]:
            if name.endswith(suffix):
                name = name[:-len(suffix)].strip().rstrip(",").strip()
        name = name.rstrip(".,").strip()

        # Check against the manager map (case-insensitive prefix matching)
        name_lower = name.lower()
        for prefix, canonical_manager in MANAGER_MAP.items():
            if name_lower.startswith(prefix) or prefix in name_lower:
                return canonical_manager

        # If no match, return cleaned name as-is
        return name

    @staticmethod
    def _clean_deal_name(issuer_name: str, title: str) -> str:
        """Build a clean, readable deal name from NPORT issuer name and title."""
        title = re.sub(r'\s+', ' ', title).strip()
        issuer = issuer_name.strip()

        # If title is empty or generic, use issuer name
        generic_titles = [
            "clo income note", "clo subordinated note", "clo equity",
            "subordinated note", "income note", "class m-1 note",
            "class m-2 note",
        ]
        if not title or title.lower().strip() in generic_titles:
            return issuer

        # If title looks like a proper deal name (contains "CLO", "Ltd", "Fund",
        # "Funding" in mixed case), use it as-is but strip trailing dates
        proper_keywords = ["CLO", "Ltd", "Fund", "Funding", "Trust", "Credit"]
        if any(kw in title for kw in proper_keywords):
            # Strip trailing maturity dates like "07/28/2031" or "01/20/2038"
            cleaned = re.sub(r'\s+\d{2}/\d{2}/\d{4}\s*$', '', title)
            return cleaned.strip()

        # If title is a CUSIP-style abbreviation (all caps + numbers),
        # build name from issuer + year from title
        if re.match(r'^[A-Z]{2,8}\d?\s+\d{4}', title):
            # Extract year and series from the title
            year_match = re.search(r'(\d{4}[-]?\d{0,3}[A-Za-z]*)', title)
            year_part = year_match.group(1) if year_match else ""

            # Clean the issuer name
            clean_issuer = issuer
            for suffix in ["Ltd", "Ltd.", "LLC", "LP", "Inc", "Inc.",
                           "LTD", ", Ltd", ", LLC"]:
                if clean_issuer.endswith(suffix):
                    clean_issuer = clean_issuer[:-len(suffix)].strip().rstrip(",").strip()

            if year_part:
                return f"{clean_issuer} {year_part}"
            return clean_issuer

        # Fallback: use title but strip dates
        cleaned = re.sub(r'\s+\d{2}/\d{2}/\d{4}\s*$', '', title)
        return cleaned.strip() if cleaned.strip() else issuer
