from datetime import date

from floor.calendar import build_checkpoints


def test_holiday_returns_no_checkpoints():
    # US Independence Day 2025 (market holiday)
    checkpoints = build_checkpoints(date(2025, 7, 4))
    assert checkpoints == []


def test_regular_day_has_open_and_close():
    checkpoints = build_checkpoints(date(2025, 7, 7))
    event_names = [c[0] for c in checkpoints]
    assert "OPEN" in event_names
    assert "CLOSE" in event_names
