"""Unit tests for core valuation helpers (no live TSETMC)."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for path in (str(ROOT), str(SRC)):
    if path not in sys.path:
        sys.path.insert(0, path)

from bi_loader import (  # noqa: E402
    is_board_variant_instrument,
    is_primary_fund_row,
    parse_tse_id,
)
from fund_calculator import classify_category, calculate_fund_value  # noqa: E402
from history_store import value_change_pct  # noqa: E402
from market_hours import gold_market_is_open  # noqa: E402


class TestBiLoader:
    def test_parse_tse_id_scientific(self) -> None:
        assert parse_tse_id(5.69874249877554e16) == 56987424987755400

    def test_parse_tse_id_nullish(self) -> None:
        assert parse_tse_id(None) is None
        assert parse_tse_id("NULL") is None

    def test_board_variants(self) -> None:
        assert is_board_variant_instrument("آتش2")
        assert is_board_variant_instrument("طلا3")
        assert is_board_variant_instrument("زر4")
        assert not is_board_variant_instrument("آتش")
        assert not is_board_variant_instrument("رز ترنج")

    def test_primary_main_market(self) -> None:
        assert is_primary_fund_row("آتش", "بازار معاملات اصلی")
        assert not is_primary_fund_row("آتش2", "بازار معاملات آد-لات")
        assert not is_primary_fund_row("آتش", "بازار معاملات بلوکی")


class TestClassify:
    def test_redemption(self) -> None:
        cat, key = classify_category(100.0, 105.0)
        assert cat == "Redemption"
        assert key == "redemption"

    def test_issue_redemption(self) -> None:
        cat, key = classify_category(110.0, 105.0)
        assert cat == "Issue/Redemption"
        assert key == "issue"

    def test_value(self) -> None:
        assert calculate_fund_value(1000, 2.5) == 2500.0


class TestMarketHours:
    def test_open_wednesday_noon(self) -> None:
        # Fixed Wednesday 2026-08-12 12:30 Tehran
        now = datetime(2026, 8, 12, 12, 30, tzinfo=ZoneInfo("Asia/Tehran"))
        assert gold_market_is_open(now) is True

    def test_closed_before_open(self) -> None:
        now = datetime(2026, 8, 12, 11, 30, tzinfo=ZoneInfo("Asia/Tehran"))
        assert gold_market_is_open(now) is False

    def test_closed_friday(self) -> None:
        now = datetime(2026, 8, 14, 13, 0, tzinfo=ZoneInfo("Asia/Tehran"))
        assert gold_market_is_open(now) is False


class TestBoardPick:
    def test_pick_main_vs_retail(self) -> None:
        from fund_calculator import _pick_from_hits
        from tsetmc_client import SearchHit

        hits = [
            SearchHit(
                ins_code="111",
                symbol="آتش",
                name="main",
                flow=1,
                flow_title="",
                market_title="بازار معاملات اصلی",
                last_date=20260811,
                is_active=True,
            ),
            SearchHit(
                ins_code="222",
                symbol="آتش",
                name="retail",
                flow=3,
                flow_title="خرده فروشی",
                market_title="خرده فروشی",
                last_date=20260811,
                is_active=True,
            ),
        ]
        assert _pick_from_hits(hits, fund_symbol="آتش", board="main") == "111"
        assert _pick_from_hits(hits, fund_symbol="آتش", board="retail") == "222"

    def test_retail_missing(self) -> None:
        from fund_calculator import _pick_from_hits
        from tsetmc_client import SearchHit

        hits = [
            SearchHit(
                ins_code="111",
                symbol="آتش",
                name="main",
                flow=1,
                flow_title="",
                market_title="بازار معاملات اصلی",
                last_date=20260811,
                is_active=True,
            ),
        ]
        assert _pick_from_hits(hits, fund_symbol="آتش", board="retail") is None


class TestHistory:
    def test_change_pct(self) -> None:
        assert value_change_pct(110, 100) == pytest.approx(10.0)
        assert value_change_pct(90, 100) == pytest.approx(-10.0)
        assert value_change_pct(100, None) is None
        assert value_change_pct(100, 0) is None
