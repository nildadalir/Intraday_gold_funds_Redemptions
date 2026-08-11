"""Persist daily fund values for day-over-day deltas."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import config

if TYPE_CHECKING:
    from fund_calculator import ValuationResult

logger = logging.getLogger(__name__)


def _history_path(day: date) -> Path:
    return config.HISTORY_DIR / f"values_{day.isoformat()}.json"


def save_daily_values(
    results: list[ValuationResult],
    *,
    day: date | None = None,
    generated_at: datetime | None = None,
) -> Path:
    """Write today's calculated values for tomorrow's DoD comparison."""
    stamp = day or date.today()
    now = generated_at or datetime.now()
    payload: dict[str, Any] = {
        "date": stamp.isoformat(),
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "funds": {
            r.symbol: {
                "value": float(r.calculated_value),
                "category": r.category,
            }
            for r in results
        },
    }
    path = _history_path(stamp)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    logger.info("Wrote daily value snapshot: %s (%s funds)", path, len(results))
    return path


def load_previous_values(before: date | None = None) -> tuple[date | None, dict[str, float]]:
    """
    Load the most recent history file strictly before `before` (default: today).

    Returns (snapshot_date, {symbol: value}).
    """
    cutoff = before or date.today()
    history_dir = config.HISTORY_DIR
    if not history_dir.exists():
        return None, {}

    candidates: list[tuple[date, Path]] = []
    for path in history_dir.glob("values_*.json"):
        try:
            day = date.fromisoformat(path.stem.replace("values_", "", 1))
        except ValueError:
            continue
        if day < cutoff:
            candidates.append((day, path))
    if not candidates:
        return None, {}

    day, path = max(candidates, key=lambda item: item[0])
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read history %s: %s", path, exc)
        return None, {}

    funds = raw.get("funds") if isinstance(raw, dict) else None
    if not isinstance(funds, dict):
        return day, {}

    out: dict[str, float] = {}
    for symbol, info in funds.items():
        if isinstance(info, dict) and "value" in info:
            try:
                out[str(symbol)] = float(info["value"])
            except (TypeError, ValueError):
                continue
        else:
            try:
                out[str(symbol)] = float(info)
            except (TypeError, ValueError):
                continue
    return day, out


def value_change_pct(current: float, previous: float | None) -> float | None:
    if previous is None or previous == 0:
        return None
    return ((current - previous) / previous) * 100.0
