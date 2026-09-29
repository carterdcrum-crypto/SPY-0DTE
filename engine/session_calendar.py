"""Verified NYSE core sessions; unknown years block new automated entries.

Source: https://www.nyse.com/markets/hours-calendars (checked 2026-09-26).
SPY options can trade later; this engine intentionally uses the core close.
Extra closures can be supplied for exchange emergency notices.
"""
from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")
_HOLIDAYS = {
    2026: "01-01 01-19 02-16 04-03 05-25 06-19 07-03 09-07 11-26 12-25",
    2027: "01-01 01-18 02-15 03-26 05-31 06-18 07-05 09-06 11-25 12-24",
    2028: "01-17 02-21 04-14 05-29 06-19 07-04 09-04 11-23 12-25",
}
_EARLY_CLOSES = {
    date(2026, 11, 27), date(2026, 12, 24), date(2027, 11, 26),
    date(2028, 7, 3), date(2028, 11, 24),
}


def known_year(day: date) -> bool:
    return day.year in _HOLIDAYS


def session_close(day: date) -> time | None:
    if not known_year(day) or day.weekday() >= 5:
        return None
    extra = {date.fromisoformat(item.strip()) for item in os.environ.get("MARKET_EXTRA_CLOSED_DATES", "").split(",") if item.strip()}
    if day in extra or day.strftime("%m-%d") in _HOLIDAYS[day.year].split():
        return None
    return time(13) if day in _EARLY_CLOSES else time(16)


def session_bounds(day: date, opens: time, closes: time, *, collection: bool = False):
    close = session_close(day)
    if close is None:
        return None
    session_end = datetime.combine(day, close, EASTERN)
    if collection:
        # Preserve a configured delayed-feed collection tail on early closes.
        tail = datetime.combine(day, closes) - datetime.combine(day, time(16))
        session_end += max(timedelta(), tail)
    configured_end = datetime.combine(day, closes, EASTERN)
    return (
        datetime.combine(day, max(time(9, 30), opens), EASTERN),
        min(session_end, configured_end),
    )


def session_status(now: datetime, opens: time, closes: time) -> dict[str, object]:
    if now.tzinfo is None:
        raise ValueError("session time must be timezone-aware")
    eastern = now.astimezone(EASTERN)
    bounds = session_bounds(eastern.date(), opens, closes)
    is_open = bounds is not None and bounds[0] <= eastern < bounds[1]
    next_open = None
    for offset in range(370):
        candidate = session_bounds(eastern.date() + timedelta(days=offset), opens, closes)
        if candidate is not None and candidate[0] > eastern:
            next_open = candidate[0].isoformat()
            break
    return {
        "state": "OPEN" if is_open else "CLOSED" if known_year(eastern.date()) else "CALENDAR_UNAVAILABLE",
        "calendar_verified_through": "2028-12-31",
        "opens_at": None if bounds is None else bounds[0].isoformat(),
        "closes_at": None if bounds is None else bounds[1].isoformat(),
        "next_session_at": next_open,
        "automatic_session_resume": True,
    }
