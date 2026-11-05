"""Unit tests for loan-level parsing and signal logic (synthetic fixtures only —
fixtures exercise code paths and are never written to the app database)."""

from datetime import date

import pandas as pd
import pytest

from src.analytics import loan_signals as ls
from src.models.schema import obligor_key, obligor_match_key
from src.scrapers.loan_nport_scraper import parse_nport_loans
from src.scrapers.ncsr_industry import normalize_sector, parse_ncsr_industries

NPORT = b"""<?xml version="1.0"?>
<edgarSubmission xmlns="http://www.sec.gov/edgar/nport"><formData>
<genInfo><repPdDate>2026-07-31</repPdDate></genInfo><invstOrSecs>
<invstOrSec><name>Acme Borrower, LLC</name><title>Acme 2025 Term Loan B</title><cusip>000000000</cusip>
<identifiers><other otherDesc="LoanX ID" value="LX1"/></identifiers>
<balance>1000000</balance><units>PA</units><currencyConditional curCd="EUR" exchangeRt="0.5"/>
<valUSD>1800000</valUSD><pctVal>0.5</pctVal><payoffProfile>Long</payoffProfile><assetCat>LON</assetCat>
<debtSec><maturityDt>2030-01-01</maturityDt><annualizedRt>8.1</annualizedRt><isDefault>N</isDefault><isPaidKind>Y</isPaidKind></debtSec>
</invstOrSec>
<invstOrSec><name>Some CLO</name><balance>5</balance><valUSD>5</valUSD><assetCat>ABS-CBDO</assetCat></invstOrSec>
</invstOrSecs></formData></edgarSubmission>"""


def test_parse_nport_loans_fx_and_flags():
    period, rows = parse_nport_loans(NPORT)
    assert period == date(2026, 7, 31)
    assert len(rows) == 1
    r = rows[0]
    assert r["currency"] == "EUR" and r["facility_key"] == "LX1"
    assert r["price"] == pytest.approx(90.0)  # 1.8m USD * 0.5 EUR/USD / 1m EUR
    assert r["is_pik"] == 1 and r["is_default"] == 0


def test_obligor_keys_match_variants():
    assert obligor_key("Medline Borrower, LP") == obligor_key("MEDLINE BORROWER LP")
    assert obligor_match_key("PROJECT ALPHA INTERMEDIA") == obligor_match_key("Project Alpha Intermediate Holding, Inc.")


def test_ncsr_industry_headings():
    html = """<table>
    <tr><td>Aerospace and Defense — 2.8%</td></tr>
    <tr><td>TransDigm, Inc., Term Loan, 6.15%, 2/28/31</td><td>2,977</td><td>2,985,073</td></tr>
    <tr><td>CAPITAL GOODS - 15.1% (9.2% of Total Investments)</td></tr>
    <tr><td>1,000</td><td>(a)</td><td>ACProducts, Inc., First Lien Term Loan</td><td>9.2</td></tr>
    <tr><td>Total Senior Loans — 99.0%</td></tr>
    <tr><td>Stray Co., Term Loan</td><td>1</td></tr></table>"""
    m = parse_ncsr_industries(html)
    assert m[obligor_key("TransDigm")] == "Aerospace and Defense"
    assert m[obligor_key("ACProducts")] == "Capital Goods"
    assert obligor_key("Stray Co") not in m
    assert normalize_sector("Software") == "Technology & Software"
    assert normalize_sector("Health Care Providers & Services") == "Healthcare"


def _pos(rows):
    df = pd.DataFrame(rows)
    df["report_date"] = pd.to_datetime(df["report_date"])
    for c, v in dict(manager="M", obligor=None, sector="Healthcare", industry="HC", is_default=0,
                     is_pik=0, maturity=pd.Timestamp("2031-01-01"), coupon=8.0, mv=0.0).items():
        if c not in df:
            df[c] = v
    df["obligor"] = df["obligor"].fillna(df["obligor_key"])
    df["facilities"] = df.get("facilities", pd.Series([{"F"}] * len(df)))
    return df


def test_position_change_actions():
    pos = _pos([
        dict(fund="A", report_date="2026-03-31", obligor_key="x", par=100, price=99),
        dict(fund="A", report_date="2026-06-30", obligor_key="x", par=50, price=82),
        dict(fund="A", report_date="2026-03-31", obligor_key="y", par=100, price=100),
        dict(fund="A", report_date="2026-06-30", obligor_key="z", par=10, price=75),
    ])
    ch = ls.latest_changes(pos).set_index("obligor_key")
    assert ch.loc["x", "action"] == "Trim" and ch.loc["x", "price_change"] == pytest.approx(-17)
    assert ch.loc["y", "action"] == "Exit"
    assert ch.loc["z", "action"] == "Add"


@pytest.mark.parametrize("row,label", [
    (dict(action="Trim", price_curr=60, price_change=-20, is_default=1), "Default / PIK"),
    (dict(action="Exit", price_prev=100, price_curr=float("nan"), facility_swap=False,
          maturity=pd.Timestamp("2031-01-01"), curr_date=pd.Timestamp("2026-06-30")), "Repaid / refi or par rotation"),
    (dict(action="Trim", price_curr=76, price_change=-8), "Discount obligation zone (<80)"),
    (dict(action="Trim", price_curr=83, price_change=-2), "CCC-migration risk (80–85)"),
    (dict(action="Trim", price_curr=93, price_change=-4, sector="Healthcare"), "Sector stress"),
])
def test_likely_reason(row, label):
    reason, _ = ls.likely_reason(row, sector_moves={"Healthcare": -2.0})
    assert reason == label


def test_opportunity_screen_scores_technical_selling():
    rows = []
    for f in ["A", "B", "C"]:
        rows.append(dict(fund=f, manager=f, report_date="2026-03-31", obligor_key="cheap", par=100, price=84))
        rows.append(dict(fund=f, manager=f, report_date="2026-06-30", obligor_key="cheap", par=60, price=78))
        rows.append(dict(fund=f, manager=f, report_date="2026-03-31", obligor_key="par", par=100, price=100))
        rows.append(dict(fund=f, manager=f, report_date="2026-06-30", obligor_key="par", par=100, price=100))
    pos = _pos(rows)
    ch = ls.latest_changes(pos)
    op = ls.opportunity_screen(pos, ch)
    assert list(op.obligor_key) == ["cheap"]   # par loan is outside the price band
    r = op.iloc[0]
    assert r.sellers == 3 and r.technical_score >= 6
    assert "discount-obligation" in r.signal_read


@pytest.mark.parametrize("row,label", [
    (dict(action="Exit", price_prev=100, facility_swap=False,
          maturity=pd.Timestamp("2031-01-01"), curr_date=pd.Timestamp("2026-06-30")), "Call / repayment"),
    (dict(action="Trim", price_curr=96, facility_swap=False,
          maturity=pd.Timestamp("2026-10-31"), curr_date=pd.Timestamp("2026-06-30")), "Call / repayment"),
    (dict(action="Trim", price_curr=99, facility_swap=True), "Reset / refi"),
    (dict(action="Exit", price_prev=82, facility_swap=False,
          maturity=pd.Timestamp("2031-01-01"), curr_date=pd.Timestamp("2026-06-30")), "Sale"),
    (dict(action="Add", price_curr=82), ""),
])
def test_coarse_loan_exit_type(row, label):
    assert ls.classify_loan_exit(row) == label


def test_position_changes_includes_coarse_exit_types():
    pos = _pos([
        dict(fund="A", report_date="2026-03-31", obligor_key="repaid", par=100, price=100),
        dict(fund="A", report_date="2026-03-31", obligor_key="sold", par=100, price=82),
        dict(fund="A", report_date="2026-03-31", obligor_key="trimmed", par=100, price=99),
        dict(fund="A", report_date="2026-06-30", obligor_key="trimmed", par=50, price=98),
    ])
    ch = ls.latest_changes(pos).set_index("obligor_key")
    assert ch.loc["repaid", "exit_type"] == "Call / repayment"
    assert ch.loc["sold", "exit_type"] == "Sale"
    assert ch.loc["trimmed", "exit_type"] == "Sale"
