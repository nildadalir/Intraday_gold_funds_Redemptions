"""HTML report generation and structured debug dumps."""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

import config
from fund_calculator import ValuationResult, calculate_fund_value, classify_category
from history_store import (
    load_previous_values,
    save_daily_values,
    value_change_pct,
)
from market_hours import gold_market_is_open, gold_market_status_message

logger = logging.getLogger(__name__)


def format_poc_output(result: ValuationResult) -> str:
    nav_issue = (
        "N/A" if result.nav_issue is None else _fmt_num(result.nav_issue)
    )
    lines = [
        "=== BI Source ===",
        f"AssetId: {result.asset_id}",
        f"InstrumentId: {result.instrument_id}",
        f"Instrument: {result.instrument}",
        f"TseId: {result.tse_id}",
        f"InstrumentCode: {result.instrument_code}",
        "",
        "=== TSETMC ===",
        f"Last Trade Price: {_fmt_num(result.last_trade_price)}",
        f"NAV Redemption: {_fmt_num(result.nav_redemption)}",
        f"NAV Issue/Redemption: {nav_issue}",
        f"Market Symbol: {result.market_symbol}",
        f"Market TseId: {result.market_tse_id}",
        f"Legal Buy Volume: {_fmt_num(result.legal_buy_volume)}",
        f"Legal Sell Volume: {_fmt_num(result.legal_sell_volume)}",
    ]
    if result.legal_volume_source:
        lines.append(f"Legal Volume Source: {result.legal_volume_source}")
    if result.legal_volume_as_of:
        lines.append(f"Legal Volume As-Of: {result.legal_volume_as_of}")
    lines.extend(
        [
            "",
            "=== Calculation ===",
            f"Selected Price: {_fmt_num(result.selected_price)}",
            f"Category: {result.category}",
            f"Calculated Value: {_fmt_num(result.calculated_value)}",
        ]
    )
    if result.warnings:
        lines.append("")
        lines.append("Warnings:")
        lines.extend(f"  - {w}" for w in result.warnings)
    return "\n".join(lines)


def build_debug_payload(result: ValuationResult) -> dict[str, Any]:
    return {
        "bi_source": {
            "AssetId": result.asset_id,
            "InstrumentId": result.instrument_id,
            "Instrument": result.instrument,
            "TseId": str(result.tse_id),
            "InstrumentCode": result.instrument_code,
        },
        "tsetmc": {
            "Last Trade Price": _json_num(result.last_trade_price),
            "NAV Redemption": _json_num(result.nav_redemption),
            "NAV Issue/Redemption": (
                None if result.nav_issue is None else _json_num(result.nav_issue)
            ),
            "Market Symbol": result.market_symbol,
            "Market TseId": result.market_tse_id,
            "Legal Buy Volume": _json_num(result.legal_buy_volume),
            "Legal Sell Volume": _json_num(result.legal_sell_volume),
            "Legal Volume Source": result.legal_volume_source or None,
            "Legal Volume As-Of": result.legal_volume_as_of,
        },
        "calculation": {
            "Selected Price": _json_num(result.selected_price),
            "Category": result.category,
            "Calculated Value": _json_num(result.calculated_value),
        },
        "warnings": list(result.warnings),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def save_debug_json(result: ValuationResult, path: Path | None = None) -> Path:
    slug = config.SYMBOL_SLUGS.get(result.symbol, result.symbol)
    out = path or (config.DEBUG_DIR / f"{slug}_result.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = build_debug_payload(result)
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("Wrote debug JSON: %s", out)
    return out


def save_poc_snapshot(result: ValuationResult, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    path = output_dir / f"poc_{result.symbol}_{stamp}.txt"
    path.write_text(format_poc_output(result), encoding="utf-8")
    return path


def result_to_dict(result: ValuationResult) -> dict[str, Any]:
    return asdict(result)


def split_and_sort(
    results: list[ValuationResult],
) -> tuple[list[ValuationResult], list[ValuationResult]]:
    redemption = [r for r in results if r.category == "Redemption"]
    issue = [r for r in results if r.category == "Issue/Redemption"]
    redemption.sort(key=lambda r: r.calculated_value, reverse=True)
    issue.sort(key=lambda r: r.calculated_value, reverse=True)
    return redemption, issue


def build_market_banner(results: list[ValuationResult]) -> dict[str, Any]:
    open_now = gold_market_is_open()
    as_of_dates = [r.legal_volume_as_of for r in results if r.legal_volume_as_of]
    as_of_label = "live / session"
    if as_of_dates:
        common = Counter(as_of_dates).most_common(1)[0][0]
        as_of_label = str(common)
        if len(set(as_of_dates)) > 1:
            as_of_label = f"{common} (mixed)"
    sources = {r.legal_volume_source for r in results if r.legal_volume_source}
    source_label = ", ".join(sorted(sources)) if sources else "n/a"
    return {
        "is_open": open_now,
        "status_label": "OPEN" if open_now else "CLOSED",
        "status_color": "#1f7a4d" if open_now else "#b02a1c",
        "message": gold_market_status_message(),
        "legal_volume_as_of": as_of_label,
        "legal_volume_source": source_label,
        "session_window": (
            f"{config.MARKET_OPEN_TIME}–{config.MARKET_CLOSE_TIME} "
            f"{config.MARKET_TIMEZONE}"
        ),
    }


def render_html_report(
    results: list[ValuationResult],
    *,
    output_path: Path,
    subtitle: str | None = None,
    generated_at: datetime | None = None,
    errors: list[dict[str, str]] | None = None,
    previous_values: dict[str, float] | None = None,
    previous_date: date | None = None,
    persist_history: bool = True,
    market_banner: dict[str, Any] | None = None,
) -> Path:
    now = generated_at or datetime.now()
    if previous_values is None:
        previous_date, previous_values = load_previous_values(now.date())
    banner = market_banner or build_market_banner(results)
    redemption, issue = split_and_sort(results)
    env = Environment(
        loader=FileSystemLoader(str(config.REPORT_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    template = env.get_template(config.REPORT_TEMPLATE_NAME)
    html = template.render(
        generated_at=now.strftime("%Y-%m-%d %H:%M:%S"),
        report_date=f"{now.strftime('%b')} {now.day}, {now.year}",
        subtitle=subtitle,
        market_banner=banner,
        previous_date=previous_date.isoformat() if previous_date else None,
        redemption_funds=[
            _row_view(r, previous_values.get(r.symbol)) for r in redemption
        ],
        issue_redemption_funds=[
            _row_view(r, previous_values.get(r.symbol)) for r in issue
        ],
        errors=errors or [],
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    logger.info("Wrote HTML report: %s", output_path)
    if persist_history and results:
        save_daily_values(results, day=now.date(), generated_at=now)
    return output_path


def build_mock_report_results() -> list[ValuationResult]:
    """
    Offline rows for HTML layout review.

    Includes MOCK_ATASH (Redemption) plus one synthetic Issue/Redemption
    row so both tables render during template review.
    """
    mock = config.MOCK_ATASH
    last = float(mock["last_price"])
    nav_red = float(mock["nav_redemption"])
    category, _ = classify_category(last, nav_red)
    buy = float(mock["legal_buy_volume"])
    selected = nav_red
    atash = ValuationResult(
        symbol=str(mock["symbol"]),
        tse_id=int(float(str(mock["tse_id"]))),
        last_trade_price=last,
        nav_redemption=nav_red,
        nav_issue=None,
        market_symbol=str(mock.get("market_symbol") or mock["symbol"]),
        market_tse_id=str(mock.get("market_tse_id") or mock["tse_id"]),
        legal_buy_volume=buy,
        legal_sell_volume=float(mock["legal_sell_volume"]),
        selected_price=selected,
        calculated_value=calculate_fund_value(buy, selected),
        category=category,
        asset_id="30018",
        instrument_id="69664",
        instrument_code="IRTKATSH0001",
        legal_volume_as_of=20260810,
        legal_volume_source="mock",
    )

    # Synthetic second row for Issue/Redemption table layout only
    issue_last = 34000.0
    issue_nav_red = 33200.0
    issue_nav = 33500.0
    issue_buy = 150000000.0
    issue_cat, _ = classify_category(issue_last, issue_nav_red)
    issue_row = ValuationResult(
        symbol="زر",
        tse_id=33254899395816171,
        last_trade_price=issue_last,
        nav_redemption=issue_nav_red,
        nav_issue=issue_nav,
        market_symbol="زر",
        market_tse_id="33254899395816171",
        legal_buy_volume=issue_buy,
        legal_sell_volume=issue_buy,
        selected_price=issue_nav,
        calculated_value=calculate_fund_value(issue_buy, issue_nav),
        category=issue_cat,
        asset_id="2869",
        instrument_id="10263",
        instrument_code="IRTKZARF0001",
        legal_volume_as_of=20260810,
        legal_volume_source="mock",
    )
    return [atash, issue_row]


def generate_mock_html_report(
    output_path: Path | None = None,
) -> Path:
    path = output_path or (config.OUTPUT_DIR / "gold_fund_report_mock.html")
    results = build_mock_report_results()
    sample_errors = [
        {
            "fund_name": "طلای زرین ملت",
            "tse_id": "NULL",
            "reason": "NULL TseId in BI export",
        }
    ]
    # Fake prior day so Δ% column is visible in mock layout.
    prev = {
        results[0].symbol: results[0].calculated_value * 0.97,
        results[1].symbol: results[1].calculated_value * 1.02,
    }
    return render_html_report(
        results,
        output_path=path,
        subtitle="Mock data — layout review only (not live market values)",
        errors=sample_errors,
        previous_values=prev,
        previous_date=date(2026, 8, 10),
        persist_history=False,
    )


def _row_view(
    result: ValuationResult, previous_value: float | None = None
) -> dict[str, Any]:
    pct = value_change_pct(result.calculated_value, previous_value)
    if pct is None:
        delta_fmt = "—"
        delta_color = "#8a97a0"
    else:
        sign = "+" if pct >= 0 else ""
        delta_fmt = f"{sign}{pct:.2f}%"
        delta_color = "#1f7a4d" if pct >= 0 else "#b02a1c"
    return {
        "asset_id": result.asset_id,
        "asset": result.asset,
        "instrument_id": result.instrument_id,
        "instrument": result.instrument,
        "price_used_fmt": _fmt_num(result.selected_price),
        "legal_volume_fmt": _fmt_num(result.legal_buy_volume),
        "value_fmt": _fmt_num(result.calculated_value),
        "delta_fmt": delta_fmt,
        "delta_color": delta_color,
    }


def _fmt_num(value: float) -> str:
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.4f}".rstrip("0").rstrip(".")


def _json_num(value: float) -> int | float:
    if float(value).is_integer():
        return int(value)
    return value
