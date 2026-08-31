"""
SQLAlchemy ORM models for CLO data.

Two core tables with real data:
  - Deal: CLO deal info (name, manager) from EDGAR NPORT-P filings
  - FundHolding: position data from public CLO equity fund portfolios

ReportSnapshot is kept for future use when trustee portal access is available.
"""

import re
from datetime import date, datetime

from sqlalchemy import (
    Column, Integer, Float, String, Date, DateTime, Text,
    ForeignKey, UniqueConstraint, Index
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


def deal_name_key(name: str) -> str:
    """
    Canonical identity key for a CLO deal.

    Funds spell the same deal differently across filings ("Ares LXI CLO Ltd" vs
    "Ares LXI CLO Ltd."), which previously created two `deals` rows and split the
    holdings between them — hiding genuine cross-fund overlap. This collapses the
    cosmetic differences: case, punctuation, spacing, and legal-entity suffixes.

    Deliberately conservative. It does NOT merge names differing in their series
    or tranche token (e.g. "2021-2A" vs "2021-2X"), which denote genuinely
    different securities — better to under-merge than to fuse two real deals.
    """
    s = (name or "").upper()
    s = re.sub(r"\b(LTD|LLC|LP|INC|DAC|CO|PLC)\b\.?", "", s)
    return re.sub(r"[^A-Z0-9]", "", s)


class Deal(Base):
    __tablename__ = "deals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    deal_name = Column(String(200), nullable=False, unique=True)
    # Canonical identity — see deal_name_key(). Deals are matched on this, not
    # on the display name, so punctuation/case variants resolve to one deal.
    name_key = Column(String(200), unique=True, index=True)
    manager = Column(String(200), nullable=False, index=True)
    trustee = Column(String(100))
    deal_size_mm = Column(Float)
    currency = Column(String(10), default="USD")
    status = Column(String(50), default="active")
    source_url = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    holdings = relationship("FundHolding", back_populates="deal",
                            order_by="FundHolding.filing_date.desc()")
    snapshots = relationship("ReportSnapshot", back_populates="deal",
                             order_by="ReportSnapshot.report_date")

    def __repr__(self):
        return f"<Deal('{self.deal_name}', manager='{self.manager}')>"


class FundHolding(Base):
    """Real position data from EDGAR NPORT-P filings."""
    __tablename__ = "fund_holdings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    deal_id = Column(Integer, ForeignKey("deals.id"), nullable=False, index=True)
    source_fund = Column(String(20), nullable=False)
    filing_date = Column(Date, nullable=False)
    par_amount = Column(Float)
    market_value = Column(Float)
    cusip = Column(String(20))
    created_at = Column(DateTime, default=datetime.utcnow)

    deal = relationship("Deal", back_populates="holdings")

    __table_args__ = (
        UniqueConstraint("deal_id", "source_fund", "filing_date", name="uq_holding"),
        Index("ix_holding_fund_date", "source_fund", "filing_date"),
    )

    @property
    def implied_price(self):
        if self.par_amount and self.par_amount > 0 and self.market_value is not None:
            return (self.market_value / self.par_amount) * 100
        return None

    def __repr__(self):
        return f"<FundHolding(fund='{self.source_fund}', deal_id={self.deal_id})>"


class ReportSnapshot(Base):
    """Reserved for future trustee report data."""
    __tablename__ = "report_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    deal_id = Column(Integer, ForeignKey("deals.id"), nullable=False, index=True)
    report_date = Column(Date, nullable=False)
    payment_date = Column(Date)
    senior_oc_ratio = Column(Float)
    senior_oc_trigger = Column(Float)
    senior_oc_cushion = Column(Float)
    mezzanine_oc_ratio = Column(Float)
    mezzanine_oc_trigger = Column(Float)
    mezzanine_oc_cushion = Column(Float)
    junior_oc_ratio = Column(Float)
    junior_oc_trigger = Column(Float)
    junior_oc_cushion = Column(Float)
    senior_ic_ratio = Column(Float)
    senior_ic_trigger = Column(Float)
    mezzanine_ic_ratio = Column(Float)
    mezzanine_ic_trigger = Column(Float)
    warf = Column(Float)
    warf_limit = Column(Float)
    was = Column(Float)
    was_minimum = Column(Float)
    diversity_score = Column(Float)
    diversity_minimum = Column(Float)
    wal = Column(Float)
    wal_limit = Column(Float)
    collateral_par = Column(Float)
    principal_cash = Column(Float)
    interest_cash = Column(Float)
    defaulted_par = Column(Float)
    ccc_bucket_pct = Column(Float)
    ccc_excess = Column(Float)
    second_lien_pct = Column(Float)
    cov_lite_pct = Column(Float)
    interest_proceeds = Column(Float)
    principal_proceeds = Column(Float)
    total_distributions = Column(Float)
    equity_distribution = Column(Float)
    equity_nav = Column(Float)
    reinvestment_amount = Column(Float)
    source_file = Column(String(500))
    created_at = Column(DateTime, default=datetime.utcnow)

    deal = relationship("Deal", back_populates="snapshots")

    __table_args__ = (
        UniqueConstraint("deal_id", "report_date", name="uq_deal_report_date"),
        Index("ix_snapshot_date", "report_date"),
    )
