"""Synthetic tests for NPORT-inferred CLO exit labels (no database writes)."""
from datetime import date

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.analytics.position_changes import classify_exit, compute_position_changes
from src.models.schema import Base, Deal, FundHolding


EMPTY_ADDS = pd.DataFrame(columns=["deal_name", "manager"])


def exit_row(name="Example CLO 2021-1 Ltd", price=45.0, manager="Example Manager"):
    return {"deal_name": name, "manager": manager, "price": price, "par": 1_000_000}


def test_near_zero_residual_is_call_or_liquidation():
    kind, why = classify_exit(exit_row("Carlyle CLO 2013-4 Ltd", 0.16), EMPTY_ADDS)
    assert kind == "Call / liquidation"
    assert "0.16" in why and "2013" in why


def test_near_par_note_or_facility_is_call_or_liquidation():
    kind, why = classify_exit(exit_row("Loan Accumulation Facility", 99.8), EMPTY_ADDS)
    assert kind == "Call / liquidation"
    assert "repayment or call" in why


def test_reset_twin_in_same_period_additions():
    adds = pd.DataFrame([{
        "deal_name": "Park Blue CLO 2022-II 2022-2A", "manager": "Park Blue CLO Management",
    }])
    kind, why = classify_exit(exit_row("Park Blue CLO 2022-II, Ltd.", 26.5, "Park Blue CLO Management"), adds)
    assert kind == "Reset / refi"
    assert "2022-2A" in why


def test_explicit_reset_suffix_matches_same_series_only():
    adds = pd.DataFrame([
        {"deal_name": "Ares XLI CLO Ltd", "manager": "Other Manager"},
        {"deal_name": "Example CLO 2021-2 Ltd", "manager": "Example Manager"},
        {"deal_name": "Example CLO 2021-1-R Ltd", "manager": "Example Manager"},
    ])
    kind, why = classify_exit(exit_row(), adds)
    assert kind == "Reset / refi" and "2021-1-R" in why


def test_legal_name_variant_is_not_a_reset():
    adds = pd.DataFrame([{"deal_name": "AMMC CLO 30, Limited", "manager": "Example Manager"}])
    kind, _ = classify_exit(exit_row("AMMC CLO 30 Ltd.", 40), adds)
    assert kind == "Sale"


def test_midrange_exit_without_twin_is_sale():
    kind, why = classify_exit(exit_row(price=38), EMPTY_ADDS)
    assert kind == "Sale" and "38.0" in why


def test_missing_mark_is_not_falsely_called_a_liquidation():
    kind, why = classify_exit(exit_row(price=None), EMPTY_ADDS)
    assert kind == "Sale" and "no usable prior mark" in why


def test_compute_position_changes_attaches_labels_to_exits():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    old = Deal(deal_name="Old CLO 2014-1 Ltd", name_key="OLDCLO20141", manager="Manager A")
    reset = Deal(deal_name="Reset CLO 2020-1 Ltd", name_key="RESETCLO20201", manager="Manager B")
    reset_r = Deal(deal_name="Reset CLO 2020-1-R Ltd", name_key="RESETCLO20201R", manager="Manager B")
    sale = Deal(deal_name="Sale CLO 2022-1 Ltd", name_key="SALECLO20221", manager="Manager C")
    session.add_all([old, reset, reset_r, sale])
    session.flush()
    for deal, mv in [(old, 100), (reset, 400_000), (sale, 500_000)]:
        session.add(FundHolding(deal_id=deal.id, source_fund="TEST", filing_date=date(2026, 3, 31),
                                par_amount=1_000_000, market_value=mv))
    session.add(FundHolding(deal_id=reset_r.id, source_fund="TEST", filing_date=date(2026, 6, 30),
                            par_amount=1_000_000, market_value=500_000))
    session.commit()

    result = compute_position_changes(session, "TEST", date(2026, 3, 31), date(2026, 6, 30))
    actual = result["exited"].set_index("deal_name")["exit_type"].to_dict()
    assert actual == {
        old.deal_name: "Call / liquidation",
        reset.deal_name: "Reset / refi",
        sale.deal_name: "Sale",
    }
    assert result["exited"]["exit_why"].str.len().gt(0).all()
    session.close()
