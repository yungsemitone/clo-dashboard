"""
High-level trustee report parser.

Takes a raw PDF trustee report, extracts structured data using the
pdf_parser module, and maps it to the database schema.
"""

import re
import logging
from datetime import date
from pathlib import Path

from sqlalchemy.orm import Session

from src.models.schema import Deal, ReportSnapshot, Holding
from src.parsers.pdf_parser import (
    extract_tables,
    extract_text,
    extract_key_value_pairs,
    parse_number,
    find_oc_ic_table,
    find_collateral_quality_table,
)

logger = logging.getLogger(__name__)


# Mapping from common report field names to our schema fields.
# Trustee reports use inconsistent labeling, so we map multiple
# possible labels to each database column.
OC_IC_FIELD_MAP = {
    "senior_oc_ratio": [
        "senior oc ratio", "class a/b oc ratio", "senior overcollateralization",
        "a/b oc test", "senior oc test", "class a oc",
    ],
    "senior_oc_trigger": [
        "senior oc trigger", "class a/b oc trigger", "senior oc minimum",
    ],
    "mezzanine_oc_ratio": [
        "mezzanine oc ratio", "class c oc ratio", "mezz oc test",
        "junior oc ratio", "class c/d oc",
    ],
    "mezzanine_oc_trigger": [
        "mezzanine oc trigger", "class c oc trigger", "mezz oc minimum",
    ],
    "senior_ic_ratio": [
        "senior ic ratio", "class a/b ic ratio", "senior interest coverage",
    ],
    "senior_ic_trigger": [
        "senior ic trigger", "class a/b ic trigger",
    ],
    "mezzanine_ic_ratio": [
        "mezzanine ic ratio", "class c ic ratio", "mezz ic test",
    ],
    "mezzanine_ic_trigger": [
        "mezzanine ic trigger", "class c ic trigger",
    ],
}

COLLATERAL_FIELD_MAP = {
    "warf": ["warf", "weighted average rating factor", "rating factor"],
    "warf_limit": ["warf limit", "warf maximum", "max warf"],
    "was": ["was", "weighted average spread", "average spread"],
    "was_minimum": ["was minimum", "minimum was", "min spread"],
    "diversity_score": ["diversity score", "diversity", "moody's diversity"],
    "diversity_minimum": ["diversity minimum", "min diversity"],
    "wal": ["wal", "weighted average life", "average life"],
    "wal_limit": ["wal limit", "wal maximum", "max wal"],
}

PORTFOLIO_FIELD_MAP = {
    "collateral_par": [
        "collateral par", "aggregate par", "total par", "portfolio par",
        "adjusted collateral principal amount",
    ],
    "principal_cash": ["principal cash", "principal account", "principal balance"],
    "interest_cash": ["interest cash", "interest account", "interest balance"],
    "defaulted_par": ["defaulted par", "defaulted obligations", "defaults"],
    "ccc_bucket_pct": ["ccc", "ccc bucket", "ccc+/below", "ccc excess"],
}


class ReportParser:
    """Parse CLO trustee reports and store structured data."""

    def __init__(self, config: dict):
        self.config = config

    def parse(self, pdf_path: Path) -> dict:
        """
        Parse a single trustee report PDF.

        Returns a dict with keys:
          - deal_info: dict of deal-level fields
          - oc_ic: dict of OC/IC test values
          - collateral: dict of collateral quality metrics
          - portfolio: dict of portfolio composition data
          - waterfall: dict of distribution/waterfall data
          - holdings: list of dicts (if asset-level detail exists)
          - source_file: str path to the source PDF
        """
        logger.info(f"Parsing: {pdf_path.name}")

        text = extract_text(pdf_path)
        tables = extract_tables(pdf_path)
        kv_pairs = extract_key_value_pairs(text)

        result = {
            "deal_info": self._extract_deal_info(text, kv_pairs),
            "oc_ic": self._extract_mapped_fields(kv_pairs, OC_IC_FIELD_MAP),
            "collateral": self._extract_mapped_fields(kv_pairs, COLLATERAL_FIELD_MAP),
            "portfolio": self._extract_mapped_fields(kv_pairs, PORTFOLIO_FIELD_MAP),
            "waterfall": self._extract_waterfall(text, kv_pairs, tables),
            "holdings": self._extract_holdings(tables),
            "source_file": str(pdf_path),
        }

        # Try OC/IC table extraction if key-value parsing missed it
        if not any(result["oc_ic"].values()):
            oc_table = find_oc_ic_table(tables)
            if oc_table is not None:
                result["oc_ic"] = self._parse_oc_ic_table(oc_table)

        return result

    def store(self, parsed: dict, session: Session):
        """Store parsed report data in the database."""
        deal_info = parsed["deal_info"]

        # Find or create the deal
        deal = session.query(Deal).filter_by(
            deal_name=deal_info.get("deal_name", "Unknown")
        ).first()

        if not deal:
            deal = Deal(
                deal_name=deal_info.get("deal_name", "Unknown"),
                manager=deal_info.get("manager", ""),
                trustee=deal_info.get("trustee", ""),
                deal_size_mm=deal_info.get("deal_size_mm"),
                source_url=parsed["source_file"],
            )
            session.add(deal)
            session.flush()  # get the deal.id

        # Create the report snapshot
        report_date = deal_info.get("report_date")
        if isinstance(report_date, str):
            try:
                from dateutil.parser import parse as parse_date
                report_date = parse_date(report_date).date()
            except (ValueError, TypeError):
                report_date = date.today()

        # Check for existing snapshot (avoid duplicates)
        existing = session.query(ReportSnapshot).filter_by(
            deal_id=deal.id, report_date=report_date
        ).first()
        if existing:
            logger.info(f"Snapshot already exists for {deal.deal_name} on {report_date}")
            return

        snapshot = ReportSnapshot(
            deal_id=deal.id,
            report_date=report_date,
            source_file=parsed["source_file"],
            # OC/IC
            **{k: v for k, v in parsed["oc_ic"].items() if v is not None},
            # Collateral
            **{k: v for k, v in parsed["collateral"].items() if v is not None},
            # Portfolio
            **{k: v for k, v in parsed["portfolio"].items() if v is not None},
            # Waterfall
            **{k: v for k, v in parsed["waterfall"].items() if v is not None},
        )

        # Compute OC cushions
        if snapshot.senior_oc_ratio and snapshot.senior_oc_trigger:
            snapshot.senior_oc_cushion = snapshot.senior_oc_ratio - snapshot.senior_oc_trigger
        if snapshot.mezzanine_oc_ratio and snapshot.mezzanine_oc_trigger:
            snapshot.mezzanine_oc_cushion = snapshot.mezzanine_oc_ratio - snapshot.mezzanine_oc_trigger

        session.add(snapshot)

        # Store holdings if available
        for holding_data in parsed.get("holdings", []):
            holding = Holding(snapshot_id=snapshot.id, **holding_data)
            session.add(holding)

    def _extract_deal_info(self, text: str, kv_pairs: dict) -> dict:
        """Extract deal-level information from the report."""
        info = {}

        # Deal name: usually in the first few lines or header
        lines = text.split("\n")[:20]
        for line in lines:
            line = line.strip()
            # Look for CLO deal name patterns
            if re.search(r"\bCLO\b|\bFunding\b|\bLoan Fund\b", line, re.I):
                if len(line) < 100:  # reasonable deal name length
                    info["deal_name"] = line
                    break

        # Manager
        for key, val in kv_pairs.items():
            if any(kw in key.lower() for kw in ["manager", "collateral manager", "portfolio manager"]):
                info["manager"] = val
                break

        # Trustee
        for key, val in kv_pairs.items():
            if "trustee" in key.lower() and "co-trustee" not in key.lower():
                info["trustee"] = val
                break

        # Report date
        for key, val in kv_pairs.items():
            if any(kw in key.lower() for kw in ["report date", "determination date", "as of"]):
                info["report_date"] = val
                break

        # Deal size
        for key, val in kv_pairs.items():
            if any(kw in key.lower() for kw in ["deal size", "original", "aggregate"]):
                info["deal_size_mm"] = parse_number(val)
                break

        return info

    def _extract_mapped_fields(self, kv_pairs: dict, field_map: dict) -> dict:
        """Extract fields using the label mapping dictionaries."""
        result = {field: None for field in field_map}

        kv_lower = {k.lower().strip(): v for k, v in kv_pairs.items()}

        for field_name, possible_labels in field_map.items():
            for label in possible_labels:
                if label in kv_lower:
                    result[field_name] = parse_number(kv_lower[label])
                    break

        return result

    def _extract_waterfall(self, text: str, kv_pairs: dict, tables: list) -> dict:
        """Extract waterfall/distribution data."""
        waterfall = {
            "interest_proceeds": None,
            "principal_proceeds": None,
            "total_distributions": None,
            "equity_distribution": None,
            "reinvestment_amount": None,
        }

        waterfall_labels = {
            "interest_proceeds": ["interest proceeds", "total interest", "interest collections"],
            "principal_proceeds": ["principal proceeds", "total principal", "principal collections"],
            "equity_distribution": ["equity distribution", "residual", "subordinated notes"],
            "reinvestment_amount": ["reinvestment", "reinvested amount"],
        }

        kv_lower = {k.lower().strip(): v for k, v in kv_pairs.items()}

        for field, labels in waterfall_labels.items():
            for label in labels:
                if label in kv_lower:
                    waterfall[field] = parse_number(kv_lower[label])
                    break

        return waterfall

    def _extract_holdings(self, tables: list) -> list[dict]:
        """
        Extract individual holding data if the report includes asset-level detail.

        Returns list of dicts matching the Holding model fields.
        """
        holdings = []

        # Look for a table that appears to be a portfolio listing
        for df in tables:
            cols_lower = [str(c).lower() for c in df.columns]

            # Check if this looks like a holdings table
            has_issuer = any("issuer" in c or "obligor" in c or "name" in c for c in cols_lower)
            has_par = any("par" in c or "principal" in c or "balance" in c for c in cols_lower)

            if has_issuer and has_par:
                for _, row in df.iterrows():
                    holding = {}
                    for col in df.columns:
                        col_lower = str(col).lower()
                        val = str(row[col]).strip() if row[col] else ""

                        if "issuer" in col_lower or "obligor" in col_lower or "name" in col_lower:
                            holding["issuer_name"] = val
                        elif "par" in col_lower or "principal" in col_lower:
                            holding["par_amount"] = parse_number(val)
                        elif "spread" in col_lower:
                            holding["spread"] = parse_number(val)
                        elif "rating" in col_lower and "moody" in col_lower:
                            holding["rating_moodys"] = val
                        elif "rating" in col_lower and ("s&p" in col_lower or "sp" in col_lower):
                            holding["rating_sp"] = val
                        elif "industry" in col_lower or "sector" in col_lower:
                            holding["industry"] = val
                        elif "type" in col_lower or "asset" in col_lower:
                            holding["asset_type"] = val

                    if holding.get("issuer_name"):
                        holdings.append(holding)

                break  # only use the first holdings table

        return holdings

    def _parse_oc_ic_table(self, df) -> dict:
        """Parse a detected OC/IC table into structured fields."""
        result = {field: None for field in OC_IC_FIELD_MAP}

        text = df.to_string().lower()

        # Try to find ratio and trigger values from the table
        for _, row in df.iterrows():
            row_text = " ".join(str(v).lower() for v in row.values if v)

            for field, labels in OC_IC_FIELD_MAP.items():
                if any(label in row_text for label in labels):
                    # Find numeric values in this row
                    numbers = re.findall(r"[\d,.]+%?", row_text)
                    nums = [parse_number(n) for n in numbers if parse_number(n)]

                    if nums and "ratio" in field:
                        result[field] = nums[0]
                    elif nums and "trigger" in field:
                        result[field] = nums[-1]  # trigger is usually the second number

        return result
