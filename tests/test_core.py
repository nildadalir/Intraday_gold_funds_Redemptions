"""Unit tests for Excel → AssetId mapping and classification (no live TSETMC)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for path in (str(ROOT), str(SRC)):
    if path not in sys.path:
        sys.path.insert(0, path)

from calculator import classify  # noqa: E402
from excel_reader import parse_tse_id  # noqa: E402
from fund_mapper import (  # noqa: E402
    FundPair,
    get_fund_by_asset_id,
    group_funds,
    is_main_board,
    is_market_board,
)
from excel_reader import InstrumentRow  # noqa: E402


class TestParseTseId:
    def test_string_full_precision(self) -> None:
        assert parse_tse_id("56987424987755487") == "56987424987755487"

    def test_null(self) -> None:
        assert parse_tse_id(None) is None
        assert parse_tse_id("NULL") is None


class TestBoardRules:
    def test_main_and_market(self) -> None:
        main = InstrumentRow(
            "69664", "آتش", "30018", "asset", "56987424987755487",
            market="بازار معاملات اصلی",
        )
        mkt = InstrumentRow(
            "69662", "آتش2", "30018", "asset", "32651481214999246",
            market="بازار معاملات آد-لات",
        )
        assert is_main_board(main)
        assert not is_market_board(main)
        assert is_market_board(mkt)
        assert not is_main_board(mkt)


class TestClassify:
    def test_redemption_le(self) -> None:
        cat, key = classify(100.0, 100.0)
        assert cat == "Redemption"
        assert key == "nav_redemption"
        cat, key = classify(99.0, 100.0)
        assert cat == "Redemption"

    def test_issue(self) -> None:
        cat, key = classify(101.0, 100.0)
        assert cat == "Issue/Redemption"
        assert key == "nav_issue"


class TestExcelMapping:
    def test_atash_group(self) -> None:
        fund = get_fund_by_asset_id("30018")
        assert fund.main.instrument == "آتش"
        assert fund.main.tse_id == "56987424987755487"
        assert fund.market.instrument == "آتش2"
        assert fund.market.tse_id == "32651481214999246"

    def test_zar_market_tseid_full(self) -> None:
        funds, _ = group_funds()
        zar = next(f for f in funds if f.main.instrument == "زر")
        assert zar.market.tse_id == "65413691615306869"
