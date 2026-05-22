"""
PDF parsing utilities for CLO trustee reports.

Trustee reports come in varying PDF formats. This module handles
extracting structured data (tables, key-value pairs) from them using
both pdfplumber (for well-structured PDFs) and tabula (for messier ones).
"""

import re
import logging
from pathlib import Path

import pdfplumber
import pandas as pd

logger = logging.getLogger(__name__)


def extract_tables(pdf_path: Path) -> list[pd.DataFrame]:
    """
    Extract all tables from a PDF using pdfplumber.

    Returns list of DataFrames, one per detected table.
    """
    tables = []

    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            page_tables = page.extract_tables()
            for table in page_tables:
                if table and len(table) > 1:
                    # Use first row as headers
                    df = pd.DataFrame(table[1:], columns=table[0])
                    df = df.dropna(how="all").reset_index(drop=True)
                    df.attrs["source_page"] = i + 1
                    tables.append(df)

    logger.info(f"Extracted {len(tables)} tables from {pdf_path.name}")
    return tables


def extract_text(pdf_path: Path) -> str:
    """Extract full text from a PDF."""
    text_parts = []

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                text_parts.append(text)

    return "\n\n".join(text_parts)


def extract_key_value_pairs(text: str) -> dict[str, str]:
    """
    Extract key-value pairs from trustee report text.

    Common patterns in trustee reports:
      "Senior OC Ratio: 128.45%"
      "WARF                    2856"
      "Diversity Score ..... 78"
    """
    pairs = {}

    # Pattern 1: "Key: Value" or "Key = Value"
    for match in re.finditer(r"([A-Za-z][A-Za-z\s/()]+?):\s*([\d,.%$()-]+)", text):
        key = match.group(1).strip()
        value = match.group(2).strip()
        pairs[key] = value

    # Pattern 2: "Key .... Value" (dot-leader pattern)
    for match in re.finditer(r"([A-Za-z][A-Za-z\s/()]+?)\s*[.]{2,}\s*([\d,.%$()-]+)", text):
        key = match.group(1).strip()
        value = match.group(2).strip()
        pairs[key] = value

    # Pattern 3: Column-aligned values (key on left, number on right)
    for match in re.finditer(r"([A-Za-z][A-Za-z\s/()]+?)\s{3,}([\d,.%$()-]+)", text):
        key = match.group(1).strip()
        value = match.group(2).strip()
        if key not in pairs:
            pairs[key] = value

    return pairs


def parse_number(value: str) -> float | None:
    """Parse a number from report text, handling %, $, commas, parens."""
    if not value:
        return None

    # Remove $ and commas
    cleaned = value.replace("$", "").replace(",", "").strip()

    # Handle percentages
    is_pct = "%" in cleaned
    cleaned = cleaned.replace("%", "")

    # Handle parentheses as negative
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = "-" + cleaned[1:-1]

    try:
        num = float(cleaned)
        return num / 100 if is_pct else num
    except ValueError:
        return None


def find_oc_ic_table(tables: list[pd.DataFrame]) -> pd.DataFrame | None:
    """
    Find the OC/IC test table among extracted tables.

    Looks for tables containing keywords like "Overcollateralization",
    "Interest Coverage", "Trigger", "Ratio", "Test".
    """
    oc_ic_keywords = [
        "overcollateral", "interest coverage", "oc test", "ic test",
        "trigger", "ratio", "cushion",
    ]

    for df in tables:
        text = " ".join(df.to_string().lower().split())
        matches = sum(1 for kw in oc_ic_keywords if kw in text)
        if matches >= 2:
            return df

    return None


def find_collateral_quality_table(tables: list[pd.DataFrame]) -> pd.DataFrame | None:
    """Find the collateral quality metrics table."""
    cq_keywords = [
        "warf", "weighted average", "diversity", "spread",
        "average life", "rating factor",
    ]

    for df in tables:
        text = " ".join(df.to_string().lower().split())
        matches = sum(1 for kw in cq_keywords if kw in text)
        if matches >= 2:
            return df

    return None
