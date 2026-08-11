"""Gold-fund session calendar (Tehran time)."""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

import config

# Python weekday: Mon=0 … Sun=6. Iran gold session: Sat–Wed.
_WEEKDAY_NAME_TO_NUM = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def _parse_hhmm(value: str) -> time:
    hour_s, minute_s = value.strip().split(":", 1)
    return time(int(hour_s), int(minute_s))


def gold_market_is_open(now: datetime | None = None) -> bool:
    """
    True when gold ETF market is in the configured Tehran session.

    Default: Saturday–Wednesday, 11:45–18:00 Asia/Tehran.
    Outside this window TSETMC live boards often show zeros / empty fields;
    prefer ClientType / price history.
    """
    tz = ZoneInfo(config.MARKET_TIMEZONE)
    current = now.astimezone(tz) if now is not None else datetime.now(tz)
    open_days = {
        _WEEKDAY_NAME_TO_NUM[d.lower()]
        for d in config.MARKET_OPEN_WEEKDAYS
        if d.lower() in _WEEKDAY_NAME_TO_NUM
    }
    if current.weekday() not in open_days:
        return False
    open_t = _parse_hhmm(config.MARKET_OPEN_TIME)
    close_t = _parse_hhmm(config.MARKET_CLOSE_TIME)
    return open_t <= current.time() < close_t


def gold_market_status_message(now: datetime | None = None) -> str:
    tz = ZoneInfo(config.MARKET_TIMEZONE)
    current = now.astimezone(tz) if now is not None else datetime.now(tz)
    days = ", ".join(config.MARKET_OPEN_WEEKDAYS)
    window = f"{config.MARKET_OPEN_TIME}–{config.MARKET_CLOSE_TIME} {config.MARKET_TIMEZONE}"
    if gold_market_is_open(current):
        return (
            f"Gold market OPEN ({current:%Y-%m-%d %H:%M %Z}); "
            f"session {days} {window}"
        )
    return (
        f"Gold market CLOSED ({current:%Y-%m-%d %H:%M %Z}); "
        f"session {days} {window} — live boards may be empty; using history fallbacks"
    )
