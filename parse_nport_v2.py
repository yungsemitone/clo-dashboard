"""
Parse NPORT-P: two approaches.
1. Download the RAW XML (not the XSLT-rendered HTML)
2. Parse the HTML version we already have for Part C holdings tables

Usage:
    export PYTHONPATH=.
    python parse_nport_v2.py
"""

import re
import requests
from pathlib import Path
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "CLO-Research-Tool/1.0 (aden.juda@nyu.edu)",
    "Accept-Encoding": "gzip, deflate",
}

RAW_DIR = Path("data/raw")


def download_raw_xml():
    """Download the raw XML NPORT file (not the XSLT version)."""
    print("=" * 60)
    print("STEP 1: Download raw NPORT-P XML")
    print("=" * 60)

    # Oxford Lane Capital - most recent NPORT-P
    # Accession: 0000894189-26-003323, CIK: 0001495222
    filings = [
        {
            "name": "Oxford Lane Capital",
            "cik": "0001495222",
            "accession": "0000894189-26-003323",
        },
        {
            "name": "Eagle Point Credit",
            "cik": "0001604174",
            "accession": "0001104659-26-021587",
        },
    ]

    downloaded = []

    for filing in filings:
        acc_clean = filing["accession"].replace("-", "")
        cik = filing["cik"]
        print(f"\n  {filing['name']}")

        # First, get the filing index to see all documents
        index_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_clean}/{filing['accession']}-index.htm"
        print(f"  Index: {index_url}")

        try:
            resp = requests.get(index_url, headers=HEADERS, timeout=30)
            if resp.status_code != 200:
                print(f"  Index failed: {resp.status_code}")
                continue

            soup = BeautifulSoup(resp.text, "lxml")
            print(f"  Documents in filing:")

            xml_doc = None
            for row in soup.select("table.tableFile tr"):
                cells = row.find_all("td")
                if len(cells) >= 4:
                    doc_type = cells[3].get_text(strip=True)
                    doc_name = cells[2].get_text(strip=True)
                    link = cells[2].find("a", href=True)
                    href = link["href"] if link else ""
                    size = cells[4].get_text(strip=True) if len(cells) > 4 else ""
                    print(f"    {doc_type:<20} {doc_name:<40} {size}")

                    # Look for the raw XML document
                    if doc_name.endswith(".xml") and "primary_doc" in doc_name.lower():
                        xml_doc = {"name": doc_name, "href": href}
                    elif doc_name.endswith(".xml") and doc_type in ["NPORT-P", ""]:
                        xml_doc = {"name": doc_name, "href": href}

            # Download the raw XML
            if xml_doc:
                url = f"https://www.sec.gov{xml_doc['href']}"
                print(f"\n  Downloading raw XML: {url}")
                resp = requests.get(url, headers=HEADERS, timeout=60)
                if resp.status_code == 200:
                    filename = f"nport_raw_{filing['name'].replace(' ', '_')}.xml"
                    filepath = RAW_DIR / filename
                    filepath.write_bytes(resp.content)
                    print(f"  Saved: {filepath} ({len(resp.content):,} bytes)")
                    downloaded.append(filepath)

                    # Quick check: is this actual XML or rendered HTML?
                    first_500 = resp.content[:500].decode("utf-8", errors="replace")
                    print(f"  Starts with: {first_500[:200]}")
                else:
                    print(f"  Download failed: {resp.status_code}")
            else:
                print("  No raw XML document found in index")

        except Exception as e:
            print(f"  Error: {e}")

    return downloaded


def parse_html_holdings():
    """Parse the XSLT-rendered HTML NPORT files for Part C holdings."""
    print("\n" + "=" * 60)
    print("STEP 2: Parse holdings from HTML NPORT files")
    print("=" * 60)

    nport_files = sorted(RAW_DIR.glob("nport_*.xml"))

    for filepath in nport_files[:1]:  # Just parse one
        print(f"\n  File: {filepath.name} ({filepath.stat().st_size:,} bytes)")

        content = filepath.read_text(errors="replace")
        soup = BeautifulSoup(content, "lxml")

        # Find all h1 headers to locate Part C
        headers = soup.find_all("h1")
        print(f"  Section headers found:")
        for h in headers:
            print(f"    {h.get_text(strip=True)[:80]}")

        # Find Part C (Schedule of Portfolio Investments)
        part_c = None
        for h in headers:
            text = h.get_text(strip=True)
            if "Part C" in text or "Schedule of Portfolio" in text:
                part_c = h
                break

        if not part_c:
            # Try finding it by searching for holding-related labels
            all_labels = soup.find_all("td", class_="label")
            holding_labels = [l for l in all_labels if "name of issuer" in l.get_text(strip=True).lower()]
            print(f"\n  'Name of issuer' labels found: {len(holding_labels)}")

            if holding_labels:
                print(f"  Extracting holdings from label/value pairs...")
                holdings = extract_holdings_from_labels(soup, holding_labels)
                return holdings
            else:
                # Last resort: search for any text containing CLO deal names
                print(f"\n  Searching for CLO names in full text...")
                text = soup.get_text(separator="\n")

                # Find lines that look like CLO deal names
                clo_lines = []
                for line in text.split("\n"):
                    line = line.strip()
                    if re.search(r'(?:CLO|Funding|Loan Fund)\s+\d{4}', line, re.I):
                        clo_lines.append(line)

                print(f"  CLO-related lines found: {len(clo_lines)}")
                for line in clo_lines[:30]:
                    print(f"    {line[:100]}")

                return []
        else:
            print(f"\n  Found Part C: {part_c.get_text(strip=True)}")


def extract_holdings_from_labels(soup, name_labels) -> list:
    """Extract holdings by finding label/value table pairs."""
    holdings = []

    for label_td in name_labels[:30]:  # Process up to 30 holdings
        holding = {}

        # The value is in the next td
        value_td = label_td.find_next_sibling("td")
        if value_td:
            holding["name"] = value_td.get_text(strip=True)

        # Navigate to subsequent rows in the same table for other fields
        row = label_td.find_parent("tr")
        if row:
            table = row.find_parent("table")
            if table:
                for tr in table.find_all("tr"):
                    cells = tr.find_all("td")
                    if len(cells) >= 2:
                        key = cells[0].get_text(strip=True).lower()
                        val = cells[1].get_text(strip=True)

                        if "title" in key:
                            holding["title"] = val
                        elif "cusip" in key:
                            holding["cusip"] = val
                        elif "balance" in key:
                            holding["balance"] = val
                        elif "value" in key and "$" not in key:
                            holding["value"] = val
                        elif "issuer type" in key or "category" in key:
                            holding["category"] = val

        if holding.get("name"):
            holdings.append(holding)

    print(f"\n  Extracted {len(holdings)} holdings:")
    for h in holdings[:20]:
        name = h.get("name", "?")
        title = h.get("title", "")
        bal = h.get("balance", "")
        print(f"    {name[:55]:<57} {title[:25]:<27} bal={bal}")

    # Filter CLO holdings
    clo = [h for h in holdings if any(kw in (h.get("name", "") + h.get("title", "")).lower()
           for kw in ["clo", "loan fund", "credit fund", "funding"])]
    print(f"\n  CLO-specific holdings: {len(clo)}")
    for h in clo:
        print(f"    {h.get('name', '?')[:70]}")

    return holdings


def parse_raw_xml(filepaths: list):
    """Parse raw NPORT XML files."""
    print("\n" + "=" * 60)
    print("STEP 3: Parse raw XML files")
    print("=" * 60)

    for filepath in filepaths:
        print(f"\n  File: {filepath.name} ({filepath.stat().st_size:,} bytes)")
        content = filepath.read_text(errors="replace")

        # Check if it's real XML or HTML
        if content.strip().startswith("<?xml") or content.strip().startswith("<edgarSubmission"):
            print("  Format: Raw XML")

            # Extract using regex (handles namespaces)
            names = re.findall(r'<(?:[\w]+:)?name>([^<]+)</(?:[\w]+:)?name>', content)
            titles = re.findall(r'<(?:[\w]+:)?title>([^<]+)</(?:[\w]+:)?title>', content)
            balances = re.findall(r'<(?:[\w]+:)?balance>([^<]+)</(?:[\w]+:)?balance>', content)
            values = re.findall(r'<(?:[\w]+:)?valUSD>([^<]+)</(?:[\w]+:)?valUSD>', content)
            cusips = re.findall(r'<(?:[\w]+:)?cusip>([^<]+)</(?:[\w]+:)?cusip>', content)

            print(f"  Found: {len(names)} names, {len(titles)} titles, {len(balances)} balances, {len(values)} values")

            # Build holdings list
            holdings = []
            for i in range(len(names)):
                h = {"name": names[i]}
                if i < len(titles):
                    h["title"] = titles[i]
                if i < len(balances):
                    h["balance"] = balances[i]
                if i < len(values):
                    h["value_usd"] = values[i]
                if i < len(cusips):
                    h["cusip"] = cusips[i]
                holdings.append(h)

            # Show CLO holdings
            clo_holdings = [h for h in holdings if any(kw in (h.get("name", "") + " " + h.get("title", "")).lower()
                           for kw in ["clo", "loan fund", "credit fund", "funding", "credit opportunities"])]

            print(f"\n  Total holdings: {len(holdings)}")
            print(f"  CLO holdings: {len(clo_holdings)}")

            for h in clo_holdings[:25]:
                name = h.get("name", "?")
                title = h.get("title", "?")
                val = h.get("value_usd", "?")
                bal = h.get("balance", "?")
                print(f"    {name[:45]:<47} {title[:30]:<32} bal={bal:<15} val=${val}")

            # Also show some non-CLO to understand the portfolio
            non_clo = [h for h in holdings if h not in clo_holdings]
            if non_clo:
                print(f"\n  Sample non-CLO holdings:")
                for h in non_clo[:5]:
                    print(f"    {h.get('name', '?')[:50]} | {h.get('title', '?')[:40]}")

        elif content.strip().startswith("<!DOCTYPE") or content.strip().startswith("<html"):
            print("  Format: HTML (XSLT-rendered) - skipping, already parsed above")
        else:
            print(f"  Unknown format. First 200 chars: {content[:200]}")


def main():
    # Step 1: Download raw XML
    raw_files = download_raw_xml()

    # Step 2: Parse HTML version for holdings
    parse_html_holdings()

    # Step 3: Parse raw XML if we got it
    if raw_files:
        parse_raw_xml(raw_files)
    else:
        print("\n" + "=" * 60)
        print("STEP 3: SKIPPED (no raw XML downloaded)")
        print("=" * 60)

    print("\nPaste everything above back to Claude.")
    print("=" * 60)


if __name__ == "__main__":
    main()
