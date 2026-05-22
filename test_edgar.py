"""
Test script: probe EDGAR for real CLO filings and test the parser.

Run this and paste the full output back to Claude so we can
tune the scraper and parser against real data.

Usage:
    export PYTHONPATH=.
    python test_edgar.py
"""

import json
import requests
import time
from pathlib import Path
from datetime import datetime, timedelta

# EDGAR requires a descriptive User-Agent
HEADERS = {
    "User-Agent": "CLO-Research-Tool/1.0 (aden.juda@nyu.edu)",
    "Accept-Encoding": "gzip, deflate",
}

RAW_DIR = Path("data/raw")
RAW_DIR.mkdir(parents=True, exist_ok=True)


def test_efts_search():
    """Test the EDGAR full-text search API for CLO filings."""
    print("=" * 60)
    print("STEP 1: Testing EDGAR Full-Text Search (EFTS)")
    print("=" * 60)

    url = "https://efts.sec.gov/LATEST/search-index"
    params = {
        "q": '"collateralized loan obligation"',
        "dateRange": "custom",
        "startdt": (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d"),
        "enddt": datetime.now().strftime("%Y-%m-%d"),
        "forms": "10-D",
    }

    print(f"\nSearching: {url}")
    print(f"Params: {json.dumps(params, indent=2)}")

    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=30)
        print(f"\nStatus: {resp.status_code}")

        if resp.status_code == 200:
            data = resp.json()
            hits = data.get("hits", {}).get("hits", [])
            total = data.get("hits", {}).get("total", {}).get("value", 0)
            print(f"Total results: {total}")
            print(f"Hits returned: {len(hits)}")

            for i, hit in enumerate(hits[:5]):
                src = hit.get("_source", {})
                print(f"\n--- Result {i+1} ---")
                print(f"  Entity: {src.get('entity_name', 'N/A')}")
                print(f"  Display names: {src.get('display_names', [])}")
                print(f"  File date: {src.get('file_date', 'N/A')}")
                print(f"  Form: {src.get('form_type', 'N/A')}")
                print(f"  Accession: {src.get('accession_no', 'N/A')}")
                print(f"  CIKs: {src.get('ciks', [])}")
                print(f"  File name: {src.get('file_name', 'N/A')}")

            return hits
        else:
            print(f"Response body: {resp.text[:500]}")
            return []

    except Exception as e:
        print(f"ERROR: {e}")
        return []


def test_efts_v2():
    """Try the newer EFTS search endpoint format."""
    print("\n" + "=" * 60)
    print("STEP 1b: Testing alternate EDGAR search endpoint")
    print("=" * 60)

    url = "https://efts.sec.gov/LATEST/search-index"
    params = {
        "q": "CLO trustee report",
        "forms": "10-D,ABS-15G",
        "dateRange": "custom",
        "startdt": (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d"),
        "enddt": datetime.now().strftime("%Y-%m-%d"),
    }

    print(f"\nSearching: {url}")
    print(f"Params: {json.dumps(params, indent=2)}")

    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=30)
        print(f"Status: {resp.status_code}")

        if resp.status_code == 200:
            data = resp.json()
            print(f"Response keys: {list(data.keys())}")
            print(f"First 500 chars: {json.dumps(data, indent=2)[:500]}")
        else:
            print(f"Response: {resp.text[:500]}")

    except Exception as e:
        print(f"ERROR: {e}")


def test_edgar_search_v3():
    """Try the EDGAR full-text search that returns HTML/JSON."""
    print("\n" + "=" * 60)
    print("STEP 1c: Testing EDGAR EFTS search API")
    print("=" * 60)

    url = "https://efts.sec.gov/LATEST/search-index"
    params = {
        "q": '"CLO" AND "trustee report"',
        "forms": "10-D",
    }

    print(f"URL: {url}")
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=30)
        print(f"Status: {resp.status_code}")
        print(f"Content-Type: {resp.headers.get('Content-Type', 'N/A')}")
        print(f"Response (first 1000 chars):\n{resp.text[:1000]}")
    except Exception as e:
        print(f"ERROR: {e}")


def test_edgar_fulltext_search():
    """The correct EDGAR full-text search endpoint."""
    print("\n" + "=" * 60)
    print("STEP 2: EDGAR Full-Text Search (correct endpoint)")
    print("=" * 60)

    # This is the actual working EDGAR search endpoint
    url = "https://efts.sec.gov/LATEST/search-index"
    params = {
        "q": "collateralized loan obligation",
        "forms": "10-D",
        "dateRange": "custom",
        "startdt": (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d"),
        "enddt": datetime.now().strftime("%Y-%m-%d"),
    }

    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=30)
        print(f"Status: {resp.status_code}")
        print(f"Headers: {dict(resp.headers)}")
        print(f"Body (first 1500 chars): {resp.text[:1500]}")
    except Exception as e:
        print(f"ERROR: {e}")

    # Also try the simpler search
    print("\n--- Trying simpler search ---")
    url2 = "https://www.sec.gov/cgi-bin/browse-edgar"
    params2 = {
        "action": "getcompany",
        "type": "10-D",
        "dateb": "",
        "owner": "include",
        "count": 10,
        "search_text": "",
        "action": "getcompany",
        "output": "atom",
    }

    try:
        resp2 = requests.get(url2, params=params2, headers=HEADERS, timeout=30)
        print(f"Status: {resp2.status_code}")
        print(f"Body (first 1500 chars): {resp2.text[:1500]}")
    except Exception as e:
        print(f"ERROR: {e}")


def download_sample_filing(hits: list):
    """Download the first available filing PDF for parser testing."""
    print("\n" + "=" * 60)
    print("STEP 3: Downloading a sample filing")
    print("=" * 60)

    if not hits:
        print("No hits from search. Trying direct 10-D filing download...")

        # Try downloading a known recent 10-D filing
        # This is a backup approach - search for recent 10-D filings via the
        # EDGAR company search
        url = "https://www.sec.gov/cgi-bin/browse-edgar"
        params = {
            "action": "getcompany",
            "type": "10-D",
            "dateb": "",
            "owner": "include",
            "count": 5,
            "output": "atom",
        }

        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=30)
            print(f"Company search status: {resp.status_code}")
            print(f"First 1000 chars: {resp.text[:1000]}")
        except Exception as e:
            print(f"ERROR: {e}")

        return None

    # Try to download from the first hit
    for hit in hits[:3]:
        src = hit.get("_source", {})
        accession = src.get("accession_no", "").replace("-", "")
        file_name = src.get("file_name", "")
        cik = src.get("ciks", [""])[0] if src.get("ciks") else ""

        if not (accession and file_name and cik):
            print(f"Incomplete metadata, skipping: {src.get('entity_name', '?')}")
            continue

        doc_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{file_name}"
        print(f"\nDownloading: {doc_url}")

        try:
            resp = requests.get(doc_url, headers=HEADERS, timeout=30)
            print(f"Status: {resp.status_code}")
            print(f"Content-Type: {resp.headers.get('Content-Type', 'N/A')}")
            print(f"Content-Length: {len(resp.content)} bytes")

            if resp.status_code == 200:
                filename = f"test_filing_{accession[:10]}.{'pdf' if 'pdf' in resp.headers.get('Content-Type', '') else 'htm'}"
                filepath = RAW_DIR / filename
                filepath.write_bytes(resp.content)
                print(f"Saved to: {filepath}")
                return filepath

        except Exception as e:
            print(f"ERROR: {e}")

        time.sleep(1)  # Rate limit

    return None


def test_parser(filepath: Path):
    """Run our parser on a downloaded filing."""
    print("\n" + "=" * 60)
    print(f"STEP 4: Testing parser on {filepath.name}")
    print("=" * 60)

    if filepath.suffix == ".pdf":
        from src.parsers.pdf_parser import extract_text, extract_tables, extract_key_value_pairs

        print("\n--- Extracting text ---")
        text = extract_text(filepath)
        print(f"Total text length: {len(text)} chars")
        print(f"First 1000 chars:\n{text[:1000]}")

        print("\n--- Extracting tables ---")
        tables = extract_tables(filepath)
        print(f"Found {len(tables)} tables")
        for i, t in enumerate(tables[:3]):
            print(f"\nTable {i+1} (page {t.attrs.get('source_page', '?')}):")
            print(f"  Shape: {t.shape}")
            print(f"  Columns: {list(t.columns)}")
            print(f"  First 3 rows:\n{t.head(3).to_string()}")

        print("\n--- Extracting key-value pairs ---")
        kv = extract_key_value_pairs(text)
        print(f"Found {len(kv)} key-value pairs:")
        for k, v in list(kv.items())[:20]:
            print(f"  {k}: {v}")

    elif filepath.suffix in (".htm", ".html"):
        print("\nFiling is HTML, not PDF.")
        content = filepath.read_text(errors="replace")
        print(f"HTML length: {len(content)} chars")
        print(f"First 1000 chars:\n{content[:1000]}")
        print("\nNote: Many 10-D filings are HTML, not PDF.")
        print("We may need an HTML parser in addition to the PDF parser.")

    else:
        print(f"Unknown file type: {filepath.suffix}")


if __name__ == "__main__":
    print("CLO Scraper - EDGAR Integration Test")
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print()

    # Step 1: Test search
    hits = test_efts_search()

    # Step 1b/1c: Try alternate endpoints if first didn't work
    if not hits:
        test_efts_v2()
        test_edgar_search_v3()
        test_edgar_fulltext_search()

    # Step 3: Download a sample
    filepath = download_sample_filing(hits)

    # Step 4: Parse it
    if filepath:
        test_parser(filepath)
    else:
        print("\n" + "=" * 60)
        print("STEP 4: SKIPPED (no file downloaded)")
        print("=" * 60)
        print("Paste all the output above back to Claude and we'll")
        print("figure out which EDGAR endpoints are working and adjust.")

    print("\n" + "=" * 60)
    print("DONE - paste everything above back to Claude")
    print("=" * 60)
