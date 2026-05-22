"""
Test script v3: Download and parse real CLO prospectus filings (FWP, 424B2).

This targets the actual CLO data available on EDGAR —
deal prospectuses with capital structure, OC/IC triggers,
collateral quality parameters, and manager info.

Usage:
    export PYTHONPATH=.
    python test_edgar_v3.py
"""

import json
import re
import requests
import time
from pathlib import Path
from datetime import datetime
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "CLO-Research-Tool/1.0 (aden.juda@nyu.edu)",
    "Accept-Encoding": "gzip, deflate",
}

RAW_DIR = Path("data/raw")
RAW_DIR.mkdir(parents=True, exist_ok=True)


def search_efts(query: str, forms: str = "", limit: int = 10) -> tuple:
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
        return [], 0
    except Exception as e:
        print(f"  Search error: {e}")
        return [], 0


def get_filing_documents(cik: str, accession: str) -> list:
    """Get the list of documents in a filing from its index page."""
    acc_clean = accession.replace("-", "")
    index_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_clean}/{accession}-index.htm"

    try:
        resp = requests.get(index_url, headers=HEADERS, timeout=30)
        if resp.status_code != 200:
            # Try alternate URL format
            index_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_clean}/{accession}-index.html"
            resp = requests.get(index_url, headers=HEADERS, timeout=30)

        if resp.status_code != 200:
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        docs = []

        for row in soup.select("table.tableFile tr"):
            cells = row.find_all("td")
            if len(cells) >= 4:
                link = cells[2].find("a", href=True)
                if link:
                    docs.append({
                        "type": cells[3].get_text(strip=True),
                        "name": cells[2].get_text(strip=True),
                        "href": link["href"],
                        "description": cells[1].get_text(strip=True) if len(cells) > 1 else "",
                    })

        return docs

    except Exception as e:
        print(f"    Index fetch error: {e}")
        return []


def download_document(href: str, filename: str) -> Path | None:
    """Download a document from EDGAR."""
    if href.startswith("/"):
        url = f"https://www.sec.gov{href}"
    else:
        url = href

    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        if resp.status_code == 200:
            filepath = RAW_DIR / filename
            filepath.write_bytes(resp.content)
            return filepath
    except Exception as e:
        print(f"    Download error: {e}")

    return None


def parse_html_filing(filepath: Path) -> dict:
    """Parse an HTML CLO prospectus filing and extract deal info."""
    content = filepath.read_text(errors="replace")
    soup = BeautifulSoup(content, "lxml")
    text = soup.get_text(separator="\n", strip=True)

    result = {
        "file": filepath.name,
        "text_length": len(text),
        "tables_found": len(soup.find_all("table")),
        "deal_info": {},
        "tranches": [],
        "collateral_params": {},
        "keywords_found": [],
    }

    # Search for CLO-related keywords
    keywords = {
        "overcollateral": "OC tests",
        "interest coverage": "IC tests",
        "weighted average rating factor": "WARF",
        "warf": "WARF",
        "weighted average spread": "WAS",
        "diversity score": "Diversity",
        "weighted average life": "WAL",
        "collateral manager": "Manager",
        "portfolio manager": "Manager",
        "reinvestment period": "Reinvestment",
        "legal maturity": "Maturity",
        "aggregate principal": "Deal size",
        "class a": "Tranche A",
        "class b": "Tranche B",
        "subordinated notes": "Equity",
        "equity tranche": "Equity",
        "ccc": "CCC bucket",
        "default": "Defaults",
        "moody": "Ratings",
        "s&p": "Ratings",
    }

    text_lower = text.lower()
    for kw, label in keywords.items():
        if kw in text_lower:
            result["keywords_found"].append(label)
    result["keywords_found"] = sorted(set(result["keywords_found"]))

    # Try to extract deal name from title or first lines
    title = soup.find("title")
    if title:
        result["deal_info"]["title"] = title.get_text(strip=True)

    # Look for deal name patterns
    clo_pattern = re.search(
        r"([A-Z][\w\s]+(?:CLO|Funding|Loan Fund|Credit)[\w\s]*(?:20\d{2}[-/]?\d{0,2})?(?:\s*(?:Ltd|LLC|Inc))?)",
        text[:3000],
    )
    if clo_pattern:
        result["deal_info"]["deal_name"] = clo_pattern.group(1).strip()

    # Look for manager
    mgr_pattern = re.search(
        r"(?:collateral manager|portfolio manager|investment manager)[:\s]*([A-Z][\w\s,&.]+?)(?:\n|\.|\()",
        text[:5000],
        re.I,
    )
    if mgr_pattern:
        result["deal_info"]["manager"] = mgr_pattern.group(1).strip()

    # Look for deal size
    size_pattern = re.search(
        r"(?:aggregate|total|initial)[\s\w]*(?:principal|notional|amount)[:\s]*\$?([\d,]+(?:\.\d+)?)\s*(million|billion)?",
        text[:5000],
        re.I,
    )
    if size_pattern:
        amount = float(size_pattern.group(1).replace(",", ""))
        unit = (size_pattern.group(2) or "").lower()
        if unit == "billion":
            amount *= 1000
        result["deal_info"]["deal_size_mm"] = amount

    # Extract tables and look for tranche/capital structure tables
    for table in soup.find_all("table"):
        table_text = table.get_text(separator=" ", strip=True).lower()

        # Capital structure table (has class names and amounts)
        if any(kw in table_text for kw in ["class a", "class b", "class c", "subordinated"]):
            rows = table.find_all("tr")
            for row in rows:
                cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
                if cells and any("class" in c.lower() or "subordinated" in c.lower() for c in cells):
                    result["tranches"].append(cells)

        # Collateral quality parameters table
        if any(kw in table_text for kw in ["warf", "diversity", "weighted average", "overcollateral"]):
            rows = table.find_all("tr")
            for row in rows:
                cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
                if len(cells) >= 2:
                    key = cells[0].lower().strip()
                    val = cells[-1].strip()
                    if any(kw in key for kw in ["warf", "diversity", "spread", "life", "overcollateral", "coverage"]):
                        result["collateral_params"][cells[0].strip()] = val

    return result


def main():
    print("CLO Scraper - Real Data Test v3")
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    # ---- Step 1: Search for CLO FWP filings ----
    print("\n" + "=" * 60)
    print("STEP 1: Search for CLO Free Writing Prospectuses (FWP)")
    print("=" * 60)

    hits, total = search_efts('"CLO" AND "collateral manager"', forms="FWP", limit=10)
    print(f"  FWP filings with CLO + collateral manager: {total}")

    if not hits:
        # Broader search
        hits, total = search_efts("CLO", forms="FWP", limit=10)
        print(f"  FWP filings with CLO (broad): {total}")

    for i, hit in enumerate(hits[:5]):
        src = hit["_source"]
        print(f"\n  {i+1}. {src.get('display_names', ['?'])[0]}")
        print(f"     Filed: {src.get('file_date', '?')} | Form: {src.get('form', '?')}")
        print(f"     CIK: {src.get('ciks', ['?'])[0]} | Accession: {src.get('adsh', '?')}")

    # ---- Step 2: Search for 424B2 CLO prospectuses ----
    print("\n" + "=" * 60)
    print("STEP 2: Search for CLO prospectus supplements (424B2)")
    print("=" * 60)

    hits_424, total_424 = search_efts('"CLO" AND "tranche"', forms="424B2", limit=10)
    print(f"  424B2 with CLO + tranche: {total_424}")

    if not hits_424:
        hits_424, total_424 = search_efts("CLO", forms="424B2", limit=10)
        print(f"  424B2 with CLO (broad): {total_424}")

    for i, hit in enumerate(hits_424[:5]):
        src = hit["_source"]
        print(f"\n  {i+1}. {src.get('display_names', ['?'])[0]}")
        print(f"     Filed: {src.get('file_date', '?')} | Accession: {src.get('adsh', '?')}")

    # ---- Step 3: Download and parse real filings ----
    print("\n" + "=" * 60)
    print("STEP 3: Download and parse filings")
    print("=" * 60)

    all_hits = hits[:3] + hits_424[:3]
    parsed_results = []

    for i, hit in enumerate(all_hits):
        src = hit["_source"]
        cik = src.get("ciks", [""])[0]
        accession = src.get("adsh", "")
        entity = src.get("display_names", ["Unknown"])[0]

        if not (cik and accession):
            continue

        print(f"\n  --- Filing {i+1}: {entity} ---")
        print(f"  Accession: {accession}")

        # Get filing documents
        docs = get_filing_documents(cik, accession)
        print(f"  Documents: {len(docs)}")
        for doc in docs:
            print(f"    {doc['type']:<15} {doc['name'][:50]}")

        # Download the main filing document (usually first .htm)
        target = None
        for doc in docs:
            if doc["name"].endswith((".htm", ".html")):
                target = doc
                break

        if not target:
            print("  No HTML document found, skipping")
            continue

        print(f"  Downloading: {target['name']}")
        filepath = download_document(target["href"], f"clo_filing_{i+1}_{accession[:10]}.htm")

        if filepath:
            print(f"  Saved: {filepath}")
            result = parse_html_filing(filepath)

            print(f"\n  Parse results:")
            print(f"    Text: {result['text_length']} chars")
            print(f"    Tables: {result['tables_found']}")
            print(f"    CLO keywords: {result['keywords_found']}")

            if result["deal_info"]:
                print(f"    Deal info: {json.dumps(result['deal_info'], indent=6)}")

            if result["tranches"]:
                print(f"    Tranches found ({len(result['tranches'])} rows):")
                for t in result["tranches"][:5]:
                    print(f"      {t}")

            if result["collateral_params"]:
                print(f"    Collateral params:")
                for k, v in result["collateral_params"].items():
                    print(f"      {k}: {v}")

            parsed_results.append(result)

        time.sleep(1)

    # ---- Summary ----
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  FWP filings found: {total}")
    print(f"  424B2 filings found: {total_424}")
    print(f"  Filings downloaded: {len(parsed_results)}")

    for r in parsed_results:
        kw = r["keywords_found"]
        info = r["deal_info"]
        name = info.get("deal_name", info.get("title", r["file"]))[:60]
        print(f"\n  {name}")
        print(f"    Keywords: {kw}")
        print(f"    Tranches: {len(r['tranches'])} rows")
        print(f"    Params: {len(r['collateral_params'])} extracted")

    print(f"\n  Files in: {RAW_DIR}")
    print("\nPaste everything above back to Claude.")
    print("=" * 60)


if __name__ == "__main__":
    main()
