"""Gold cash-market hours in Tehran (config.yaml ``market``)."""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

import config

_WEEKDAY_NAME_TO_INDEX = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}


def _parse_hhmm(value: str) -> time:
    hour_s, minute_s = value.strip().split(":", 1)
    return time(int(hour_s), int(minute_s))


def _weekday_indices(spec: list[str | int]) -> set[int]:
    out: set[int] = set()
    for item in spec:
        if isinstance(item, int):
            out.add(int(item) % 7)
            continue
        key = str(item).strip().lower()[:3]
        if key not in _WEEKDAY_NAME_TO_INDEX:
            raise ValueError(f"Unknown weekday in market.open_weekdays: {item!r}")
        out.add(_WEEKDAY_NAME_TO_INDEX[key])
    return out


def tehran_now(at: datetime | None = None) -> datetime:
    tz = ZoneInfo(config.MARKET_TIMEZONE)
    if at is None:
        return datetime.now(tz)
    if at.tzinfo is None:
        return at.replace(tzinfo=tz)
    return at.astimezone(tz)


def is_gold_trading_weekday(at: datetime | None = None) -> bool:
    """True Saturday–Wednesday (gold cash week), ignoring clock time."""
    now = tehran_now(at)
    return now.weekday() in _weekday_indices(config.MARKET_OPEN_WEEKDAYS)


def is_gold_market_open(at: datetime | None = None) -> bool:
    """Saturday–Wednesday, 12:00 inclusive to 18:00 exclusive, Tehran time."""
    now = tehran_now(at)
    if now.weekday() not in _weekday_indices(config.MARKET_OPEN_WEEKDAYS):
        return False
    start = _parse_hhmm(config.MARKET_OPEN_TIME)
    end = _parse_hhmm(config.MARKET_CLOSE_TIME)
    return start <= now.time() < end
