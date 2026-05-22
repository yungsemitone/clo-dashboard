"""
CLO Manager performance tracking and analytics.

Analyzes manager-level performance across deals using stored
report snapshots: OC cushion trends, default rates, equity
distributions, and collateral quality over time.
"""

import logging
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import func
from sqlalchemy.orm import Session

from src.models.schema import Deal, ReportSnapshot

logger = logging.getLogger(__name__)


class ManagerTracker:
    """Analyze and rank CLO manager performance."""

    def __init__(self, session: Session):
        self.session = session

    def leaderboard(self, top_n: int = 20) -> str:
        """
        Generate a manager leaderboard ranked by composite score.

        Scoring factors:
          - Average Senior OC cushion (higher is better)
          - Default rate (lower is better)
          - Equity distribution consistency
          - Number of deals managed
        """
        managers = self._get_manager_stats()

        if not managers:
            return "No data available. Run a scrape first."

        df = pd.DataFrame(managers)

        # Composite score (simple weighted average for now)
        # Normalize each metric to 0-1 range, then weight
        for col in ["avg_oc_cushion", "avg_equity_dist", "deal_count"]:
            if col in df.columns and df[col].notna().any():
                col_min = df[col].min()
                col_max = df[col].max()
                if col_max > col_min:
                    df[f"{col}_norm"] = (df[col] - col_min) / (col_max - col_min)
                else:
                    df[f"{col}_norm"] = 0.5

        if "avg_default_rate" in df.columns and df["avg_default_rate"].notna().any():
            col_min = df["avg_default_rate"].min()
            col_max = df["avg_default_rate"].max()
            if col_max > col_min:
                df["default_norm"] = 1 - (df["avg_default_rate"] - col_min) / (col_max - col_min)
            else:
                df["default_norm"] = 0.5

        score_cols = [c for c in df.columns if c.endswith("_norm")]
        if score_cols:
            df["composite_score"] = df[score_cols].mean(axis=1)
            df = df.sort_values("composite_score", ascending=False)

        df = df.head(top_n)

        # Format output
        output = f"\n{'Rank':<5} {'Manager':<35} {'Deals':<7} {'Avg OC Cushion':<16} {'Avg Default %':<15} {'Score':<8}\n"
        output += "-" * 86 + "\n"

        for i, (_, row) in enumerate(df.iterrows(), 1):
            oc = f"{row.get('avg_oc_cushion', 0):.2f}%" if pd.notna(row.get("avg_oc_cushion")) else "N/A"
            default = f"{row.get('avg_default_rate', 0):.2f}%" if pd.notna(row.get("avg_default_rate")) else "N/A"
            score = f"{row.get('composite_score', 0):.3f}" if "composite_score" in row else "N/A"
            output += f"{i:<5} {row['manager']:<35} {int(row['deal_count']):<7} {oc:<16} {default:<15} {score:<8}\n"

        return output

    def manager_report(self, manager_name: str) -> str:
        """Generate a detailed report for a single manager."""
        deals = (
            self.session.query(Deal)
            .filter(Deal.manager.ilike(f"%{manager_name}%"))
            .all()
        )

        if not deals:
            return f"No deals found for manager matching '{manager_name}'"

        manager = deals[0].manager  # canonical name
        output = f"\n{'=' * 60}\n"
        output += f"  Manager Report: {manager}\n"
        output += f"{'=' * 60}\n\n"

        output += f"  Active Deals: {len(deals)}\n"
        total_size = sum(d.deal_size_mm or 0 for d in deals)
        output += f"  Total AUM: ${total_size:,.0f}M\n\n"

        output += f"  {'Deal Name':<35} {'Size ($M)':<12} {'Latest OC':<12} {'Status':<10}\n"
        output += f"  {'-' * 69}\n"

        for deal in deals:
            size = f"${deal.deal_size_mm:,.0f}" if deal.deal_size_mm else "N/A"
            latest_oc = "N/A"
            if deal.snapshots:
                latest = deal.snapshots[-1]
                if latest.senior_oc_ratio:
                    latest_oc = f"{latest.senior_oc_ratio:.2f}%"

            output += f"  {deal.deal_name:<35} {size:<12} {latest_oc:<12} {deal.status:<10}\n"

        # OC cushion trend
        output += f"\n  OC Cushion Trend (last 6 reports):\n"
        output += f"  {'-' * 50}\n"

        for deal in deals:
            if not deal.snapshots:
                continue

            recent = deal.snapshots[-6:]
            cushions = [
                f"{s.senior_oc_cushion:.2f}%"
                for s in recent
                if s.senior_oc_cushion is not None
            ]
            if cushions:
                output += f"  {deal.deal_name[:30]:<32} {' -> '.join(cushions)}\n"

        return output

    def oc_trend(self, manager_name: str = None, months: int = 12) -> pd.DataFrame:
        """
        Get OC cushion trends over time.

        Returns a DataFrame with columns: deal_name, manager, report_date, senior_oc_cushion
        """
        cutoff = date.today() - timedelta(days=months * 30)

        query = (
            self.session.query(
                Deal.deal_name,
                Deal.manager,
                ReportSnapshot.report_date,
                ReportSnapshot.senior_oc_ratio,
                ReportSnapshot.senior_oc_trigger,
                ReportSnapshot.senior_oc_cushion,
            )
            .join(ReportSnapshot, Deal.id == ReportSnapshot.deal_id)
            .filter(ReportSnapshot.report_date >= cutoff)
        )

        if manager_name:
            query = query.filter(Deal.manager.ilike(f"%{manager_name}%"))

        query = query.order_by(Deal.manager, Deal.deal_name, ReportSnapshot.report_date)

        rows = query.all()
        return pd.DataFrame(rows, columns=[
            "deal_name", "manager", "report_date",
            "senior_oc_ratio", "senior_oc_trigger", "senior_oc_cushion",
        ])

    def _get_manager_stats(self) -> list[dict]:
        """Aggregate manager-level statistics from the database."""
        deals = self.session.query(Deal).all()

        manager_map = {}
        for deal in deals:
            mgr = deal.manager
            if not mgr:
                continue

            if mgr not in manager_map:
                manager_map[mgr] = {
                    "manager": mgr,
                    "deal_count": 0,
                    "oc_cushions": [],
                    "default_rates": [],
                    "equity_dists": [],
                }

            manager_map[mgr]["deal_count"] += 1

            if deal.snapshots:
                latest = deal.snapshots[-1]

                if latest.senior_oc_cushion is not None:
                    manager_map[mgr]["oc_cushions"].append(latest.senior_oc_cushion)

                if latest.collateral_par and latest.defaulted_par:
                    default_rate = (latest.defaulted_par / latest.collateral_par) * 100
                    manager_map[mgr]["default_rates"].append(default_rate)

                if latest.equity_distribution is not None:
                    manager_map[mgr]["equity_dists"].append(latest.equity_distribution)

        stats = []
        for mgr, data in manager_map.items():
            cushions = data["oc_cushions"]
            defaults = data["default_rates"]
            equity = data["equity_dists"]

            stats.append({
                "manager": data["manager"],
                "deal_count": data["deal_count"],
                "avg_oc_cushion": sum(cushions) / len(cushions) if cushions else None,
                "avg_default_rate": sum(defaults) / len(defaults) if defaults else None,
                "avg_equity_dist": sum(equity) / len(equity) if equity else None,
            })

        return stats
