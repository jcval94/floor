from __future__ import annotations

import calendar as pycalendar
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from floor.schemas import EventType

ET = ZoneInfo("America/New_York")


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    count = 0
    for day in range(1, 32):
        try:
            d = date(year, month, day)
        except ValueError:
            break
        if d.weekday() == weekday:
            count += 1
            if count == n:
                return d
    raise ValueError("Invalid nth weekday")


def _last_weekday(year: int, month: int, weekday: int) -> date:
    last_day = pycalendar.monthrange(year, month)[1]
    for day in range(last_day, 0, -1):
        d = date(year, month, day)
        if d.weekday() == weekday:
            return d
    raise ValueError("Invalid last weekday")


def _observed(d: date) -> date:
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def is_us_market_holiday(d: date) -> bool:
    y = d.year
    holidays = {
        _observed(date(y, 1, 1)),  # New Year
        _nth_weekday(y, 1, 0, 3),  # MLK
        _nth_weekday(y, 2, 0, 3),  # Presidents
        _last_weekday(y, 5, 0),  # Memorial
        _observed(date(y, 7, 4)),
        _nth_weekday(y, 9, 0, 1),  # Labor
        _nth_weekday(y, 11, 3, 4),  # Thanksgiving
        _observed(date(y, 12, 25)),
    }
    return d in holidays


def is_early_close(d: date) -> bool:
    y = d.year
    thanksgiving = _nth_weekday(y, 11, 3, 4)
    day_after_thanksgiving = thanksgiving + timedelta(days=1)
    christmas_eve = date(y, 12, 24)
    if christmas_eve.weekday() >= 5:
        return False
    return d in {day_after_thanksgiving, christmas_eve}


def build_checkpoints(session_day: date) -> list[tuple[EventType, datetime]]:
    if session_day.weekday() >= 5 or is_us_market_holiday(session_day):
        return []

    market_open = datetime.combine(session_day, time(hour=9, minute=30), tzinfo=ET)
    close_time = time(hour=13, minute=0) if is_early_close(session_day) else time(hour=16, minute=0)
    market_close = datetime.combine(session_day, close_time, tzinfo=ET)

    checkpoints: list[tuple[EventType, datetime]] = [
        ("OPEN", market_open),
        ("OPEN_PLUS_2H", market_open + timedelta(hours=2)),
        ("OPEN_PLUS_4H", market_open + timedelta(hours=4)),
        ("OPEN_PLUS_6H", market_open + timedelta(hours=6)),
        ("CLOSE", market_close),
    ]
    return [(name, ts) for name, ts in checkpoints if market_open <= ts <= market_close]


def nearest_event_type(now: datetime | None = None) -> EventType | None:
    now = now or datetime.now(tz=ET)
    checkpoints = build_checkpoints(now.date())
    if not checkpoints:
        return None
    for event, ts in checkpoints:
        if ts >= now:
            return event
    return "CLOSE"
