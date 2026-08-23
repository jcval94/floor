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
            current = date(year, month, day)
        except ValueError:
            break
        if current.weekday() == weekday:
            count += 1
            if count == n:
                return current
    raise ValueError("Invalid nth weekday")


def _last_weekday(year: int, month: int, weekday: int) -> date:
    last_day = pycalendar.monthrange(year, month)[1]
    for day in range(last_day, 0, -1):
        current = date(year, month, day)
        if current.weekday() == weekday:
            return current
    raise ValueError("Invalid last weekday")


def _observed(day: date) -> date:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def _easter_sunday(year: int) -> date:
    """Gregorian computus, used to derive Good Friday."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    weekday_offset = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * weekday_offset) // 451
    month = (h + weekday_offset - 7 * m + 114) // 31
    day = ((h + weekday_offset - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def is_us_market_holiday(day: date) -> bool:
    year = day.year
    holidays = {
        _observed(date(year, 1, 1)),
        _nth_weekday(year, 1, 0, 3),  # MLK
        _nth_weekday(year, 2, 0, 3),  # Presidents Day
        _easter_sunday(year) - timedelta(days=2),  # Good Friday
        _last_weekday(year, 5, 0),  # Memorial Day
        _observed(date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1),  # Labor Day
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving
        _observed(date(year, 12, 25)),
    }
    if year >= 2022:
        holidays.add(_observed(date(year, 6, 19)))  # Juneteenth
    return day in holidays


def is_market_session(day: date) -> bool:
    return day.weekday() < 5 and not is_us_market_holiday(day)


def previous_market_session(day: date) -> date:
    cursor = day - timedelta(days=1)
    while not is_market_session(cursor):
        cursor -= timedelta(days=1)
    return cursor


def is_early_close(day: date) -> bool:
    year = day.year
    thanksgiving = _nth_weekday(year, 11, 3, 4)
    day_after_thanksgiving = thanksgiving + timedelta(days=1)
    christmas_eve = date(year, 12, 24)
    if christmas_eve.weekday() >= 5:
        return day == day_after_thanksgiving
    return day in {day_after_thanksgiving, christmas_eve}


def build_checkpoints(session_day: date) -> list[tuple[EventType, datetime]]:
    if not is_market_session(session_day):
        return []
    market_open = datetime.combine(session_day, time(hour=9, minute=30), tzinfo=ET)
    close_time = time(hour=13) if is_early_close(session_day) else time(hour=16)
    market_close = datetime.combine(session_day, close_time, tzinfo=ET)
    checkpoints: list[tuple[EventType, datetime]] = [
        ("OPEN", market_open),
        ("OPEN_PLUS_2H", market_open + timedelta(hours=2)),
        ("OPEN_PLUS_4H", market_open + timedelta(hours=4)),
        ("OPEN_PLUS_6H", market_open + timedelta(hours=6)),
        ("CLOSE", market_close),
    ]
    return [(name, timestamp) for name, timestamp in checkpoints if market_open <= timestamp <= market_close]


def nearest_event_type(now: datetime | None = None) -> EventType | None:
    now = now or datetime.now(tz=ET)
    checkpoints = build_checkpoints(now.date())
    if not checkpoints:
        return None
    for event, timestamp in checkpoints:
        if timestamp >= now:
            return event
    return "CLOSE"
