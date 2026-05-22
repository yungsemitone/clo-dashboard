"""
Parse the NPORT-P files we already downloaded.

The files are XHTML using SEC namespaces (m1:, ns1:).
Holdings are in <m1:invstOrSec> elements with fields like
<m1:name>, <m1:title>, <m1:balance>, <m1:valUSD>, etc.

Usage:
    export PYTHONPATH=.
    python parse_nport.py
"""

import re
from pathlib import Path
from bs4 import BeautifulSoup

RAW_DIR = Path("data/raw")


def parse_nport_file(filepath: Path) -> list:
    """Parse an NPORT-P XHTML file for portfolio holdings."""
    print(f"\n  Parsing: {filepath.name} ({filepath.stat().st_size:,} bytes)")

    content = filepath.read_text(errors="replace")

    # First, let's see what namespace prefixes are used
    ns_matches = re.findall(r'xmlns:([\w]+)="([^"]+)"', content[:2000])
    print(f"  Namespaces found: {ns_matches}")

    # Find the namespace prefix used for NPORT elements
    nport_prefix = None
    for prefix, uri in ns_matches:
        if "nport" in uri.lower():
            nport_prefix = prefix
            break
    print(f"  NPORT prefix: {nport_prefix}")

    # Method 1: Regex-based extraction (most reliable for namespaced XHTML)
    # Look for investment/security blocks
    holdings = []

    # Pattern for the full investment block
    if nport_prefix:
        # Try namespaced tags
        block_pattern = re.compile(
            rf'<{nport_prefix}:invstOrSec>(.*?)</{nport_prefix}:invstOrSec>',
            re.DOTALL | re.IGNORECASE,
        )
        blocks = block_pattern.findall(content)
        print(f"  Investment blocks found ({nport_prefix}:invstOrSec): {len(blocks)}")

        if not blocks:
            # Try alternate tag names
            for tag in ["invstOrSec", "invstOrSecCond", "invstOrSecAlt"]:
                blocks = re.findall(
                    rf'<{nport_prefix}:{tag}>(.*?)</{nport_prefix}:{tag}>',
                    content, re.DOTALL | re.I,
                )
                if blocks:
                    print(f"  Found blocks with tag: {nport_prefix}:{tag}: {len(blocks)}")
                    break

    if not blocks:
        # Try without namespace prefix
        blocks = re.findall(r'<invstOrSec>(.*?)</invstOrSec>', content, re.DOTALL | re.I)
        print(f"  Blocks without prefix: {len(blocks)}")

    # If still nothing, let's look at what tags ARE in the file
    if not blocks:
        print("\n  No standard blocks found. Sampling tags from the file...")
        all_tags = re.findall(r'<([\w:]+)[>\s]', content[:50000])
        unique_tags = sorted(set(all_tags))
        print(f"  Unique tags (first 50): {unique_tags[:50]}")

        # Look for any tag containing 'inv', 'sec', 'hold', 'name', 'bal'
        relevant = [t for t in unique_tags if any(kw in t.lower() for kw in
                     ['inv', 'sec', 'hold', 'name', 'bal', 'val', 'cusip', 'isin', 'title'])]
        print(f"  Relevant tags: {relevant}")

        # Try to extract using whatever tags exist
        if relevant:
            name_tag = next((t for t in relevant if 'name' in t.lower()), None)
            title_tag = next((t for t in relevant if 'title' in t.lower()), None)
            bal_tag = next((t for t in relevant if 'bal' in t.lower()), None)
            val_tag = next((t for t in relevant if 'val' in t.lower()), None)
            cusip_tag = next((t for t in relevant if 'cusip' in t.lower()), None)

            print(f"  Using tags - name: {name_tag}, title: {title_tag}, balance: {bal_tag}, value: {val_tag}, cusip: {cusip_tag}")

            if name_tag:
                names = re.findall(rf'<{re.escape(name_tag)}>(.*?)</{re.escape(name_tag)}>', content, re.I)
                titles = re.findall(rf'<{re.escape(title_tag)}>(.*?)</{re.escape(title_tag)}>', content, re.I) if title_tag else []
                bals = re.findall(rf'<{re.escape(bal_tag)}>(.*?)</{re.escape(bal_tag)}>', content, re.I) if bal_tag else []
                vals = re.findall(rf'<{re.escape(val_tag)}>(.*?)</{re.escape(val_tag)}>', content, re.I) if val_tag else []
                cusips = re.findall(rf'<{re.escape(cusip_tag)}>(.*?)</{re.escape(cusip_tag)}>', content, re.I) if cusip_tag else []

                print(f"  Extracted: {len(names)} names, {len(titles)} titles, {len(bals)} balances, {len(vals)} values")

                for i in range(len(names)):
                    holding = {"name": names[i].strip()}
                    if i < len(titles):
                        holding["title"] = titles[i].strip()
                    if i < len(bals):
                        holding["balance"] = bals[i].strip()
                    if i < len(vals):
                        holding["value_usd"] = vals[i].strip()
                    if i < len(cusips):
                        holding["cusip"] = cusips[i].strip()
                    holdings.append(holding)

        return holdings

    # Parse each investment block
    for block in blocks:
        holding = {}

        def extract(tag_name):
            # Try with namespace prefix
            for prefix in [nport_prefix + ":", ""]:
                match = re.search(
                    rf'<{prefix}{tag_name}>(.*?)</{prefix}{tag_name}>',
                    block, re.I | re.DOTALL,
                )
                if match:
                    return match.group(1).strip()
            return None

        holding["name"] = extract("name")
        holding["title"] = extract("title")
        holding["cusip"] = extract("cusip")
        holding["balance"] = extract("balance")
        holding["value_usd"] = extract("valUSD")
        holding["pct_value"] = extract("pctVal")
        holding["asset_type"] = extract("assetCat")
        holding["issuer_type"] = extract("issuerCat")

        # Clean up
        holding = {k: v for k, v in holding.items() if v}
        if holding:
            holdings.append(holding)

    return holdings


def main():
    print("NPORT-P Holdings Parser")
    print("=" * 60)

    # Find all downloaded NPORT files
    nport_files = sorted(RAW_DIR.glob("nport_*.xml"))
    print(f"Found {len(nport_files)} NPORT files in {RAW_DIR}")

    if not nport_files:
        print("No NPORT files found. Run test_edgar_v4.py first.")
        return

    for filepath in nport_files:
        holdings = parse_nport_file(filepath)
        print(f"\n  Total holdings: {len(holdings)}")

        if not holdings:
            # Last resort: dump a section of the file so we can see the structure
            print("\n  DUMPING FILE SAMPLE (lines 100-300):")
            lines = filepath.read_text(errors="replace").split("\n")
            for line in lines[100:300]:
                stripped = line.strip()
                if stripped and not stripped.startswith("<!--"):
                    print(f"    {stripped[:120]}")
            continue

        # Filter for CLO-related holdings
        clo_holdings = []
        other_holdings = []

        for h in holdings:
            text = (h.get("name", "") + " " + h.get("title", "")).lower()
            if any(kw in text for kw in ["clo", "loan fund", "credit fund", "funding",
                                          "loan trust", "credit partners", "credit opportunities"]):
                clo_holdings.append(h)
            else:
                other_holdings.append(h)

        print(f"  CLO holdings: {len(clo_holdings)}")
        print(f"  Other holdings: {len(other_holdings)}")

        if clo_holdings:
            print(f"\n  CLO Holdings:")
            for h in clo_holdings[:25]:
                name = h.get("name", "?")
                title = h.get("title", "")
                bal = h.get("balance", "?")
                val = h.get("value_usd", "?")
                print(f"    {name[:50]:<52} title={title[:30]:<32} bal={bal:<15} val=${val}")

        if other_holdings:
            print(f"\n  Sample other holdings (first 10):")
            for h in other_holdings[:10]:
                name = h.get("name", "?")
                title = h.get("title", "")
                print(f"    {name[:60]:<62} title={title[:40]}")

    print("\n" + "=" * 60)
    print("Paste everything above back to Claude.")
    print("=" * 60)


if __name__ == "__main__":
    main()
