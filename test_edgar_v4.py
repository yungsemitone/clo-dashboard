"""
Test v4: Pull real CLO portfolio data from public CLO equity fund NPORT-P filings.

Oxford Lane Capital (OXLC), Eagle Point Credit (ECC), Pearl Diver (PDCC),
and OFS Credit (OCCI) all file quarterly NPORT-P reports with full
portfolio holdings — every CLO deal they own, with par values, market
values, managers, and deal details. This is structured XML data.

Also re-parses the Pearl Diver 424B2 we already downloaded to inspect
the actual table contents.

Usage:
    export PYTHONPATH=.
    python test_edgar_v4.py
"""

import json
import re
import requests
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "CLO-Research-Tool/1.0 (aden.juda@nyu.edu)",
    "Accept-Encoding": "gzip, deflate",
}

RAW_DIR = Path("data/raw")
RAW_DIR.mkdir(parents=True, exist_ok=True)

# Public CLO equity funds that file NPORT-P
CLO_FUNDS = [
    {"name": "Oxford Lane Capital", "cik": "0001495222"},
    {"name": "Eagle Point Credit", "cik": "0001604174"},
    {"name": "OFS Credit Company", "cik": "0001716951"},
    {"name": "Pearl Diver Credit", "cik": "0001998043"},
    {"name": "Priority Income Fund", "cik": "0001554625"},
]


def get_recent_filings(cik: str, form_type: str, count: int = 5) -> list:
    """Get recent filings for a CIK via EDGAR submissions API."""
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
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
            if form == form_type and i < len(accessions):
                filings.append({
                    "form": form,
                    "date": dates[i] if i < len(dates) else "",
                    "accession": accessions[i] if i < len(accessions) else "",
                    "primary_doc": primary_docs[i] if i < len(primary_docs) else "",
                })
                if len(filings) >= count:
                    break

        return filings

    except Exception as e:
        print(f"    Error: {e}")
        return []


def download_nport(cik: str, accession: str, primary_doc: str) -> Path | None:
    """Download an NPORT-P filing."""
    acc_clean = accession.replace("-", "")

    # Try the primary document first
    url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_clean}/{primary_doc}"
    print(f"    Trying: {url}")

    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        if resp.status_code == 200:
            ext = Path(primary_doc).suffix or ".xml"
            filename = f"nport_{cik}_{accession[:10]}{ext}"
            filepath = RAW_DIR / filename
            filepath.write_bytes(resp.content)
            print(f"    Downloaded: {filepath} ({len(resp.content)} bytes)")
            return filepath
        else:
            print(f"    Status {resp.status_code}")
    except Exception as e:
        print(f"    Error: {e}")

    return None


def parse_nport_xml(filepath: Path) -> list:
    """Parse an NPORT-P XML filing for portfolio holdings."""
    content = filepath.read_text(errors="replace")

    # NPORT files can be XML or HTML wrapping XML
    holdings = []

    # Try XML parsing
    try:
        # Remove namespace prefixes for easier parsing
        content_clean = re.sub(r'xmlns[^"]*"[^"]*"', '', content)
        content_clean = re.sub(r'</?[\w]+:', '</', content_clean) if '</' in content_clean else content_clean

        root = ET.fromstring(content_clean)

        # Look for invstOrSec (investment or security) elements
        for elem in root.iter():
            if elem.tag and "invstOrSec" in elem.tag.lower():
                holding = {}
                for child in elem:
                    tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                    tag = tag.lower()

                    if "name" in tag:
                        holding["name"] = child.text
                    elif "title" in tag:
                        holding["title"] = child.text
                    elif "cusip" in tag:
                        holding["cusip"] = child.text
                    elif tag == "balance" or "bal" in tag:
                        holding["balance"] = child.text
                    elif "val" in tag and "usd" in tag.lower():
                        holding["value_usd"] = child.text
                    elif "pctval" in tag:
                        holding["pct_value"] = child.text
                    elif "issuercat" in tag:
                        holding["category"] = child.text
                    elif "assetcat" in tag:
                        holding["asset_type"] = child.text

                if holding.get("name") or holding.get("title"):
                    holdings.append(holding)

    except ET.ParseError:
        pass

    # Fallback: try BeautifulSoup parsing
    if not holdings:
        soup = BeautifulSoup(content, "lxml")
        text = soup.get_text(separator="\n", strip=True)

        # Look for holding patterns
        print(f"    XML parse got 0 holdings, trying text extraction...")
        print(f"    Content type check - starts with: {content[:200]}")

        # Look for specific NPORT XML tags via regex
        name_matches = re.findall(r"<name>([^<]+)</name>", content, re.I)
        title_matches = re.findall(r"<title>([^<]+)</title>", content, re.I)
        balance_matches = re.findall(r"<balance>([^<]+)</balance>", content, re.I)
        value_matches = re.findall(r"<valUSD>([^<]+)</valUSD>", content, re.I)

        print(f"    Regex found: {len(name_matches)} names, {len(title_matches)} titles, {len(balance_matches)} balances, {len(value_matches)} values")

        if name_matches:
            for i in range(min(len(name_matches), len(title_matches) if title_matches else len(name_matches))):
                holding = {"name": name_matches[i]}
                if i < len(title_matches):
                    holding["title"] = title_matches[i]
                if i < len(balance_matches):
                    holding["balance"] = balance_matches[i]
                if i < len(value_matches):
                    holding["value_usd"] = value_matches[i]
                holdings.append(holding)

    return holdings


def inspect_existing_filing():
    """Re-examine the Pearl Diver 424B2 we already downloaded."""
    filepath = RAW_DIR / "clo_filing_6_0001214659.htm"
    if not filepath.exists():
        print("  Pearl Diver 424B2 not found in data/raw/. Skipping.")
        return

    print("  Re-parsing Pearl Diver 424B2 prospectus...")
    content = filepath.read_text(errors="replace")
    soup = BeautifulSoup(content, "lxml")
    tables = soup.find_all("table")

    print(f"  Total tables: {len(tables)}")
    print(f"  Inspecting tables for CLO data...\n")

    interesting_tables = []
    for i, table in enumerate(tables):
        text = table.get_text(separator=" ", strip=True).lower()

        # Score each table for CLO relevance
        score = 0
        matched = []
        for kw in ["class a", "class b", "class c", "class d", "class e",
                    "overcollateral", "interest coverage", "warf", "diversity",
                    "weighted average", "par value", "principal amount",
                    "tranche", "coupon", "spread", "maturity", "subordinat",
                    "senior secured", "equity", "waterfall"]:
            if kw in text:
                score += 1
                matched.append(kw)

        if score >= 2:
            interesting_tables.append((i, score, matched, table))

    print(f"  Found {len(interesting_tables)} CLO-relevant tables\n")

    for idx, (table_num, score, matched, table) in enumerate(interesting_tables[:10]):
        rows = table.find_all("tr")
        print(f"  --- Table #{table_num} (score={score}, keywords={matched}) ---")
        print(f"  Rows: {len(rows)}")

        # Print first 8 rows
        for j, row in enumerate(rows[:8]):
            cells = [c.get_text(strip=True)[:50] for c in row.find_all(["td", "th"])]
            if any(c for c in cells):  # skip empty rows
                print(f"    Row {j}: {cells}")

        print()


def main():
    print("CLO Scraper - NPORT-P Portfolio Holdings Test")
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")

    # ---- Step 1: Check existing downloaded filing ----
    print("=" * 60)
    print("STEP 1: Inspect Pearl Diver 424B2 tables")
    print("=" * 60)
    inspect_existing_filing()

    # ---- Step 2: Find NPORT-P filings for CLO funds ----
    print("\n" + "=" * 60)
    print("STEP 2: Find NPORT-P filings from CLO equity funds")
    print("=" * 60)

    nport_filings = []

    for fund in CLO_FUNDS:
        print(f"\n  {fund['name']} (CIK: {fund['cik']})")

        filings = get_recent_filings(fund["cik"], "NPORT-P", count=2)
        if not filings:
            # Try NPORT-P/A (amended)
            filings = get_recent_filings(fund["cik"], "NPORT-P/A", count=2)

        if filings:
            for f in filings:
                print(f"    {f['form']} filed {f['date']} | {f['primary_doc']}")
                nport_filings.append({**f, "fund": fund})
        else:
            print(f"    No NPORT-P filings found")

            # Check what forms they DO file
            alt_forms = get_recent_filings(fund["cik"], "N-CSR", count=1)
            if alt_forms:
                print(f"    But found N-CSR: {alt_forms[0]['date']}")

        time.sleep(0.5)

    # ---- Step 3: Download and parse the most recent NPORT-P ----
    print("\n" + "=" * 60)
    print("STEP 3: Download and parse NPORT-P filings")
    print("=" * 60)

    all_holdings = []

    for filing in nport_filings[:3]:  # Download up to 3
        fund = filing["fund"]
        print(f"\n  Downloading: {fund['name']} ({filing['date']})")

        filepath = download_nport(
            fund["cik"],
            filing["accession"],
            filing["primary_doc"],
        )

        if filepath:
            holdings = parse_nport_xml(filepath)
            print(f"    Holdings parsed: {len(holdings)}")

            # Show CLO-related holdings
            clo_holdings = []
            for h in holdings:
                name = (h.get("name", "") + " " + h.get("title", "")).lower()
                if any(kw in name for kw in ["clo", "loan fund", "credit fund", "funding"]):
                    clo_holdings.append(h)

            print(f"    CLO-related holdings: {len(clo_holdings)}")

            for h in clo_holdings[:15]:
                name = h.get("name", h.get("title", "?"))
                val = h.get("value_usd", h.get("balance", "?"))
                print(f"      {name[:60]:<62} ${val}")

            all_holdings.extend(clo_holdings)

        time.sleep(1)

    # ---- Summary ----
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  NPORT-P filings found: {len(nport_filings)}")
    print(f"  Total CLO holdings extracted: {len(all_holdings)}")

    if all_holdings:
        # Unique deal names
        names = set()
        for h in all_holdings:
            name = h.get("name", h.get("title", ""))
            if name:
                names.add(name)
        print(f"  Unique CLO deals: {len(names)}")
        print(f"\n  Sample deals:")
        for name in sorted(names)[:20]:
            print(f"    {name}")

    print(f"\n  Files in: {RAW_DIR}")
    print("\nPaste everything above back to Claude.")
    print("=" * 60)


if __name__ == "__main__":
    main()
