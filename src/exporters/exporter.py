"""
Export CLO data from SQLite to CSV and Excel formats.
"""

import logging
from pathlib import Path
from datetime import datetime

import pandas as pd
from sqlalchemy.orm import Session

from src.models.schema import Deal, ReportSnapshot, Holding

logger = logging.getLogger(__name__)


class DataExporter:
    """Export database contents to CSV and Excel."""

    def __init__(self, config: dict, session: Session):
        self.config = config
        self.session = session

    def _deals_df(self) -> pd.DataFrame:
        """Build a DataFrame of all deals."""
        deals = self.session.query(Deal).all()
        return pd.DataFrame([{
            "deal_id": d.id,
            "deal_name": d.deal_name,
            "manager": d.manager,
            "trustee": d.trustee,
            "original_close_date": d.original_close_date,
            "reinvestment_end_date": d.reinvestment_end_date,
            "legal_maturity": d.legal_maturity,
            "deal_size_mm": d.deal_size_mm,
            "status": d.status,
        } for d in deals])

    def _snapshots_df(self) -> pd.DataFrame:
        """Build a DataFrame of all report snapshots with deal info."""
        snapshots = (
            self.session.query(ReportSnapshot, Deal)
            .join(Deal, ReportSnapshot.deal_id == Deal.id)
            .all()
        )

        rows = []
        for snap, deal in snapshots:
            row = {
                "deal_name": deal.deal_name,
                "manager": deal.manager,
                "report_date": snap.report_date,
                "senior_oc_ratio": snap.senior_oc_ratio,
                "senior_oc_trigger": snap.senior_oc_trigger,
                "senior_oc_cushion": snap.senior_oc_cushion,
                "mezzanine_oc_ratio": snap.mezzanine_oc_ratio,
                "mezzanine_oc_trigger": snap.mezzanine_oc_trigger,
                "mezzanine_oc_cushion": snap.mezzanine_oc_cushion,
                "senior_ic_ratio": snap.senior_ic_ratio,
                "mezzanine_ic_ratio": snap.mezzanine_ic_ratio,
                "warf": snap.warf,
                "was": snap.was,
                "diversity_score": snap.diversity_score,
                "wal": snap.wal,
                "collateral_par": snap.collateral_par,
                "defaulted_par": snap.defaulted_par,
                "ccc_bucket_pct": snap.ccc_bucket_pct,
                "interest_proceeds": snap.interest_proceeds,
                "principal_proceeds": snap.principal_proceeds,
                "equity_distribution": snap.equity_distribution,
            }
            rows.append(row)

        return pd.DataFrame(rows)

    def _manager_summary_df(self) -> pd.DataFrame:
        """Build a manager-level summary DataFrame."""
        deals = self.session.query(Deal).all()
        manager_data = {}

        for deal in deals:
            mgr = deal.manager
            if mgr not in manager_data:
                manager_data[mgr] = {
                    "manager": mgr,
                    "total_deals": 0,
                    "total_aum_mm": 0,
                    "avg_oc_cushion": [],
                }

            manager_data[mgr]["total_deals"] += 1
            if deal.deal_size_mm:
                manager_data[mgr]["total_aum_mm"] += deal.deal_size_mm

            # Get latest snapshot for OC cushion
            if deal.snapshots:
                latest = deal.snapshots[-1]
                if latest.senior_oc_cushion is not None:
                    manager_data[mgr]["avg_oc_cushion"].append(latest.senior_oc_cushion)

        rows = []
        for mgr, data in manager_data.items():
            cushions = data["avg_oc_cushion"]
            rows.append({
                "manager": data["manager"],
                "total_deals": data["total_deals"],
                "total_aum_mm": data["total_aum_mm"],
                "avg_senior_oc_cushion": sum(cushions) / len(cushions) if cushions else None,
            })

        return pd.DataFrame(rows).sort_values("total_deals", ascending=False)

    def to_csv(self, output_dir: str):
        """Export all tables to CSV files."""
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d")

        deals_df = self._deals_df()
        snapshots_df = self._snapshots_df()
        managers_df = self._manager_summary_df()

        deals_df.to_csv(out / f"deals_{timestamp}.csv", index=False)
        snapshots_df.to_csv(out / f"report_snapshots_{timestamp}.csv", index=False)
        managers_df.to_csv(out / f"manager_summary_{timestamp}.csv", index=False)

        logger.info(f"CSV export complete: {out}")

    def to_excel(self, output_dir: str):
        """Export all tables to a multi-sheet Excel workbook."""
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d")
        filepath = out / f"clo_data_{timestamp}.xlsx"

        with pd.ExcelWriter(filepath, engine="openpyxl") as writer:
            self._deals_df().to_excel(writer, sheet_name="Deals", index=False)
            self._snapshots_df().to_excel(writer, sheet_name="Report Snapshots", index=False)
            self._manager_summary_df().to_excel(writer, sheet_name="Manager Summary", index=False)

        logger.info(f"Excel export complete: {filepath}")
