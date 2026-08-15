"""HTML report renderer (Turquoise template — layout preserved)."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

import config
from calculator import ErrorRecord, ValuationResult

logger = logging.getLogger(__name__)

TEHRAN = ZoneInfo("Asia/Tehran")


def _fmt_int(value: float | int) -> str:
    return f"{int(round(float(value))):,}"


def _fmt_price(value: float) -> str:
    if float(value).is_integer():
        return _fmt_int(value)
    return f"{float(value):,.2f}"


def _row_dict(result: ValuationResult) -> dict[str, str]:
    return {
        "asset_id": result.asset_id,
        "asset": result.asset,
        "instrument_id": result.instrument_id,
        "instrument": result.instrument,
        "price_used_fmt": _fmt_price(result.selected_price),
        "legal_volume_fmt": _fmt_int(result.legal_buy_volume),
        "value_fmt": _fmt_int(result.calculated_value),
        "delta_fmt": "—",
        "delta_color": "#8a97a0",
    }


def render_html_report(
    results: list[ValuationResult],
    errors: list[ErrorRecord],
    *,
    output_path: Path | None = None,
    generated_at: datetime | None = None,
) -> Path:
    now = generated_at or datetime.now(tz=TEHRAN)
    redemption = sorted(
        [r for r in results if r.category == "Redemption"],
        key=lambda r: r.calculated_value,
        reverse=True,
    )
    issue = sorted(
        [r for r in results if r.category == "Issue/Redemption"],
        key=lambda r: r.calculated_value,
        reverse=True,
    )

    env = Environment(
        loader=FileSystemLoader(str(config.REPORT_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    template = env.get_template(config.TEMPLATE_PATH.name)
    html = template.render(
        generated_at=now.strftime("%Y-%m-%d %H:%M:%S"),
        report_date=now.strftime("%Y-%m-%d"),
        subtitle=None,
        market_banner=None,
        previous_date=None,
        redemption_funds=[_row_dict(r) for r in redemption],
        issue_redemption_funds=[_row_dict(r) for r in issue],
        errors=[
            {
                "fund_name": e.fund_name,
                "tse_id": e.tse_id,
                "reason": e.reason,
            }
            for e in errors
        ],
    )

    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out = output_path or (
        config.OUTPUT_DIR
        / f"intra-day-gold-redemptions-{now.strftime('%Y-%m-%d')}.html"
    )
    out.write_text(html, encoding="utf-8")
    logger.info("Wrote HTML report: %s", out)
    return out
