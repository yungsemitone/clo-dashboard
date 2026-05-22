"""
Test script v2: Find real CLO data on EDGAR and test parsing.

Approaches:
  1. Search EFTS for known CLO managers/issuers
  2. Search for ABS-EE filings (asset-level CLO data, post-Dodd-Frank)
  3. Download and parse an actual structured finance 10-D filing
  4. Test direct trustee portal access (US Bank, BNY Mellon)

Usage:
    export PYTHONPATH=.
    python test_edgar_v2.py
"""

import json
import re
import requests
import time
from pathlib import Path
from datetime import datetime, timedelta
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "CLO-Research-Tool/1.0 (aden.juda@nyu.edu)",
    "Accept-Encoding": "gzip, deflate",
}

RAW_DIR = Path("data/raw")
RAW_DIR.mkdir(parents=True, exist_ok=True)

# Known CLO issuers/vehicles that file on EDGAR
KNOWN_CLO_ISSUERS = [
    "Ares CLO",
    "CIFC Funding",
    "Carlyle US CLO",
    "Octagon Investment Partners",
    "Sound Point CLO",
    "Golub Capital Partners CLO",
    "Bain Capital Credit CLO",
    "Owl Rock CLO",
    "Palmer Square CLO",
    "Dryden Senior Loan Fund",
    "Magnetite CLO",
    "Allegro CLO",
    "Apidos CLO",
    "Benefit Street Partners CLO",
    "BlueMountain CLO",
]


def search_efts(query: str, forms: str = "", limit: int = 10) -> list:
    """Search EDGAR EFTS and return hits."""
    url = "https://efts.sec.gov/LATEST/search-index"
    params = {"q": query, "from": 0, "size": limit}
    if forms:
        params["forms"] = forms

    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            hits = data.get("hits", {}).get("hits", [])
            total = data.get("hits", {}).get("total", {}).get("value", 0)
            return hits, total
        else:
            return [], 0
    except Exception as e:
        print(f"  ERROR: {e}")
        return [], 0


def step1_search_clo_issuers():
    """Search EDGAR for known CLO issuers."""
    print("=" * 60)
    print("STEP 1: Search for known CLO issuers on EDGAR")
    print("=" * 60)

    found = []
    for issuer in KNOWN_CLO_ISSUERS[:8]:  # Test first 8
        hits, total = search_efts(f'"{issuer}"', limit=3)
        if total > 0:
            src = hits[0]["_source"]
            print(f"\n  {issuer}: {total} filings found")
            print(f"    Latest: {src.get('form', '?')} filed {src.get('file_date', '?')}")
            print(f"    Entity: {src.get('display_names', ['?'])[0]}")
            print(f"    CIK: {src.get('ciks', ['?'])[0]}")
            found.append({
                "issuer": issuer,
                "total": total,
                "cik": src.get("ciks", [""])[0],
                "form": src.get("form", ""),
                "latest_hit": src,
            })
        else:
            print(f"\n  {issuer}: 0 filings")

        time.sleep(0.5)

    return found


def step2_search_abs_ee():
    """Search for ABS-EE filings (asset-level CLO data)."""
    print("\n" + "=" * 60)
    print("STEP 2: Search for ABS-EE filings (asset-level data)")
    print("=" * 60)

    hits, total = search_efts("CLO", forms="ABS-EE", limit=10)
    print(f"\n  ABS-EE with 'CLO': {total} results")

    for i, hit in enumerate(hits[:5]):
        src = hit["_source"]
        print(f"\n  --- Result {i+1} ---")
        print(f"    Entity: {src.get('display_names', ['?'])[0]}")
        print(f"    Filed: {src.get('file_date', '?')}")
        print(f"    CIK: {src.get('ciks', ['?'])[0]}")
        print(f"    Accession: {src.get('adsh', '?')}")

    # Also try other form types
    for form in ["SF-3", "FWP", "424B2", "ABS-15G"]:
        hits2, total2 = search_efts("CLO", forms=form, limit=3)
        print(f"\n  {form} with 'CLO': {total2} results")
        if hits2:
            src = hits2[0]["_source"]
            print(f"    Latest: {src.get('display_names', ['?'])[0]} ({src.get('file_date', '?')})")
        time.sleep(0.5)

    return hits, total


def step3_download_filing():
    """Download an actual filing document and inspect its format."""
    print("\n" + "=" * 60)
    print("STEP 3: Download and inspect real filings")
    print("=" * 60)

    # Search for recent structured finance 10-D filings
    hits, total = search_efts(
        "collateralized loan obligation",
        forms="10-D",
        limit=5,
    )
    print(f"\n  10-D filings mentioning CLOs: {total}")

    downloaded = []

    for i, hit in enumerate(hits[:3]):
        src = hit["_source"]
        cik = src.get("ciks", [""])[0]
        accession = src.get("adsh", "").replace("-", "")
        accession_dash = src.get("adsh", "")
        file_name = src.get("file_name", "")
        entity = src.get("display_names", ["Unknown"])[0]
        file_type = src.get("file_type", "")

        print(f"\n  --- Filing {i+1}: {entity} ---")
        print(f"    Form: {src.get('form', '?')}, Type: {file_type}")
        print(f"    Filed: {src.get('file_date', '?')}")
        print(f"    Accession: {accession_dash}")
        print(f"    File: {file_name}")

        if not (cik and accession):
            print("    SKIP: incomplete metadata")
            continue

        # First, get the filing index page to see all documents
        index_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{accession_dash}-index.htm"
        print(f"    Index URL: {index_url}")

        try:
            resp = requests.get(index_url, headers=HEADERS, timeout=30)
            print(f"    Index status: {resp.status_code}")

            if resp.status_code == 200:
                # Parse index to find all filing documents
                soup = BeautifulSoup(resp.text, "lxml")
                print(f"    Documents in filing:")

                for row in soup.select("table.tableFile tr"):
                    cells = row.find_all("td")
                    if len(cells) >= 4:
                        doc_type = cells[3].get_text(strip=True)
                        doc_name = cells[2].get_text(strip=True)
                        link = cells[2].find("a", href=True)
                        doc_href = link["href"] if link else ""
                        print(f"      {doc_type:<15} {doc_name:<40} {doc_href[:60]}")

                # Download the main filing document
                if file_name:
                    doc_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{file_name}"
                    print(f"\n    Downloading: {doc_url}")

                    doc_resp = requests.get(doc_url, headers=HEADERS, timeout=30)
                    print(f"    Status: {doc_resp.status_code}")
                    print(f"    Content-Type: {doc_resp.headers.get('Content-Type', '?')}")
                    print(f"    Size: {len(doc_resp.content)} bytes")

                    ext = Path(file_name).suffix or ".htm"
                    local_name = f"filing_{i+1}_{accession[:10]}{ext}"
                    local_path = RAW_DIR / local_name
                    local_path.write_bytes(doc_resp.content)
                    print(f"    Saved: {local_path}")
                    downloaded.append(local_path)

        except Exception as e:
            print(f"    ERROR: {e}")

        time.sleep(1)

    return downloaded


def step4_test_trustee_portals():
    """Test direct access to trustee reporting portals."""
    print("\n" + "=" * 60)
    print("STEP 4: Test trustee portal access")
    print("=" * 60)

    portals = [
        {
            "name": "US Bank Pivot (main page)",
            "url": "https://pivot.usbank.com",
        },
        {
            "name": "US Bank Structured Finance",
            "url": "https://www.usbank.com/corporate-and-commercial-banking/corporate-trust/structured-finance.html",
        },
        {
            "name": "BNY Mellon GCT Investor Reporting",
            "url": "https://gctinvestorreporting.bnymellon.com",
        },
        {
            "name": "Computershare Corporate Trust",
            "url": "https://www-us.computershare.com/Investor/",
        },
    ]

    for portal in portals:
        print(f"\n  Testing: {portal['name']}")
        print(f"  URL: {portal['url']}")

        try:
            resp = requests.get(
                portal["url"], headers=HEADERS, timeout=15,
                allow_redirects=True,
            )
            print(f"  Status: {resp.status_code}")
            print(f"  Final URL: {resp.url}")
            print(f"  Content-Type: {resp.headers.get('Content-Type', '?')}")
            print(f"  Size: {len(resp.content)} bytes")

            # Check if it's a login page or has useful content
            text = resp.text.lower()
            if "login" in text or "sign in" in text or "password" in text:
                print("  ** REQUIRES LOGIN **")
            if "report" in text and "download" in text:
                print("  ** Has report/download links **")

            # Look for any PDF or report links
            soup = BeautifulSoup(resp.text, "lxml")
            pdf_links = soup.find_all("a", href=re.compile(r"\.pdf", re.I))
            report_links = soup.find_all("a", href=re.compile(r"report|trustee|statement", re.I))
            print(f"  PDF links found: {len(pdf_links)}")
            print(f"  Report-related links: {len(report_links)}")

            if report_links[:3]:
                print("  Sample links:")
                for link in report_links[:3]:
                    print(f"    {link.get_text(strip=True)[:50]}: {link['href'][:80]}")

        except Exception as e:
            print(f"  ERROR: {e}")

        time.sleep(1)


def step5_parse_downloaded(files: list):
    """Parse any downloaded files."""
    print("\n" + "=" * 60)
    print("STEP 5: Parse downloaded filings")
    print("=" * 60)

    if not files:
        print("  No files to parse.")
        return

    for filepath in files:
        print(f"\n  --- Parsing: {filepath.name} ---")
        print(f"  Extension: {filepath.suffix}")
        print(f"  Size: {filepath.stat().st_size} bytes")

        content = filepath.read_bytes()

        if filepath.suffix in (".htm", ".html", ".xml"):
            text = content.decode("utf-8", errors="replace")
            soup = BeautifulSoup(text, "lxml")

            # Extract visible text
            body_text = soup.get_text(separator="\n", strip=True)
            print(f"  Text length: {len(body_text)} chars")
            print(f"  First 1500 chars of text:\n{'='*40}")
            print(body_text[:1500])
            print(f"{'='*40}")

            # Look for tables
            tables = soup.find_all("table")
            print(f"\n  Tables found: {len(tables)}")

            # Look for CLO-related keywords
            keywords = [
                "overcollateral", "oc test", "ic test", "warf",
                "diversity", "weighted average", "collateral",
                "tranche", "waterfall", "subordinat", "equity",
                "default", "ccc", "spread", "par value",
            ]
            found_kw = [kw for kw in keywords if kw in body_text.lower()]
            print(f"  CLO keywords found: {found_kw}")

        elif filepath.suffix == ".pdf":
            try:
                from src.parsers.pdf_parser import extract_text, extract_tables, extract_key_value_pairs

                text = extract_text(filepath)
                tables = extract_tables(filepath)
                kv = extract_key_value_pairs(text)

                print(f"  Text length: {len(text)} chars")
                print(f"  Tables: {len(tables)}")
                print(f"  Key-value pairs: {len(kv)}")
                print(f"  First 1000 chars:\n{text[:1000]}")

                if kv:
                    print(f"\n  Key-value pairs (first 15):")
                    for k, v in list(kv.items())[:15]:
                        print(f"    {k}: {v}")
            except Exception as e:
                print(f"  PDF parse error: {e}")

        else:
            print(f"  Unknown format: {filepath.suffix}")


if __name__ == "__main__":
    print("CLO Scraper - EDGAR Integration Test v2")
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print()

    # Step 1: Search for known CLO issuers
    found_issuers = step1_search_clo_issuers()

    # Step 2: Search for ABS-EE and other CLO-specific form types
    abs_hits, abs_total = step2_search_abs_ee()

    # Step 3: Download actual filings
    downloaded = step3_download_filing()

    # Step 4: Test trustee portal access
    step4_test_trustee_portals()

    # Step 5: Parse whatever we downloaded
    step5_parse_downloaded(downloaded)

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  CLO issuers found on EDGAR: {len(found_issuers)}")
    print(f"  ABS-EE filings found: {abs_total}")
    print(f"  Files downloaded: {len(downloaded)}")
    print(f"  Files saved in: {RAW_DIR}")
    print()
    print("Paste everything above back to Claude.")
    print("=" * 60)
