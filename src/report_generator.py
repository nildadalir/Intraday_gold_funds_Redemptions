"""HTML report renderer (Turquoise template — layout preserved)."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape
import jdatetime

import config
from calculator import ErrorRecord, ValuationResult

logger = logging.getLogger(__name__)

TEHRAN = ZoneInfo("Asia/Tehran")

# TSETMC monetary fields (NAV, last price, volume × NAV) are Rial.
# Report display: Toman = Rial / 10; Billion Toman = Rial / 10 / 1e9.
RIAL_PER_TOMAN = 10
TOMAN_PER_BILLION = 1_000_000_000


def rial_to_toman(rial: float) -> float:
    return float(rial) / RIAL_PER_TOMAN


def rial_to_billion_toman(rial: float) -> float:
    return rial_to_toman(rial) / TOMAN_PER_BILLION


def _fmt_int(value: float | int) -> str:
    return f"{int(round(float(value))):,}"


def _fmt_billion_toman(value: float) -> str:
    rounded = round(float(value), 3)
    text = f"{rounded:,.3f}".rstrip("0").rstrip(".")
    return text


def _to_jalali(dt: datetime) -> jdatetime.datetime:
    naive = dt.replace(tzinfo=None) if dt.tzinfo is not None else dt
    return jdatetime.datetime.fromgregorian(datetime=naive)


def _fmt_jalali_date(dt: datetime) -> str:
    jdt = _to_jalali(dt)
    return f"{jdt.year:04d}-{jdt.month:02d}-{jdt.day:02d}"


def _fmt_jalali_datetime(dt: datetime) -> str:
    jdt = _to_jalali(dt)
    return (
        f"{jdt.year:04d}-{jdt.month:02d}-{jdt.day:02d} "
        f"{jdt.hour:02d}:{jdt.minute:02d}:{jdt.second:02d}"
    )


def _row_dict(result: ValuationResult) -> dict[str, str]:
    return {
        "instrument": result.instrument,
        "price_used_fmt": _fmt_int(rial_to_toman(result.selected_price)),
        "institutional_volume_fmt": _fmt_int(result.legal_buy_volume),
        "institutional_value_fmt": _fmt_billion_toman(
            rial_to_billion_toman(result.calculated_value)
        ),
    }


def report_filename(at: datetime | None = None) -> str:
    now = at or datetime.now(tz=TEHRAN)
    if now.tzinfo is None:
        now = now.replace(tzinfo=TEHRAN)
    else:
        now = now.astimezone(TEHRAN)
    return f"intra-day-gold-redemptions-{now.strftime('%Y-%m-%d')}.html"


def session_date(at: datetime | None = None) -> str:
    now = at or datetime.now(tz=TEHRAN)
    if now.tzinfo is None:
        now = now.replace(tzinfo=TEHRAN)
    else:
        now = now.astimezone(TEHRAN)
    return now.strftime("%Y-%m-%d")


def all_calculated_values_zero(results: list[ValuationResult]) -> bool:
    """True when there is nothing to report, or every fund value is 0."""
    if not results:
        return True
    return all(float(r.calculated_value) == 0.0 for r in results)


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
        generated_at=_fmt_jalali_datetime(now),
        report_date=_fmt_jalali_date(now),
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

    filename = report_filename(now)
    if output_path is None:
        if all_calculated_values_zero(results):
            config.OUTPUT_ERROR_DIR.mkdir(parents=True, exist_ok=True)
            out = config.OUTPUT_ERROR_DIR / filename
        else:
            config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            out = config.OUTPUT_DIR / filename
    else:
        out = output_path
        out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    logger.info("Wrote HTML report: %s", out)
    return out
