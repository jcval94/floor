from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from utils.market_session import detect_event, get_session_info


ET = ZoneInfo("America/New_York")


def test_session_gating_known_checkpoints() -> None:
    assert detect_event(datetime(2026, 3, 12, 9, 30, tzinfo=ET), tolerance_minutes=0) == "OPEN"
    assert detect_event(datetime(2026, 3, 12, 11, 30, tzinfo=ET), tolerance_minutes=0) == "OPEN_PLUS_2H"
    assert detect_event(datetime(2026, 3, 12, 13, 30, tzinfo=ET), tolerance_minutes=0) == "OPEN_PLUS_4H"
    assert detect_event(datetime(2026, 3, 12, 15, 30, tzinfo=ET), tolerance_minutes=0) == "OPEN_PLUS_6H"
    assert detect_event(datetime(2026, 3, 12, 16, 0, tzinfo=ET), tolerance_minutes=0) == "CLOSE"


def test_session_gating_is_stable_for_same_timestamp() -> None:
    now = datetime(2026, 3, 12, 11, 45, tzinfo=ET)
    assert detect_event(now, tolerance_minutes=20) == detect_event(now, tolerance_minutes=20)


def test_session_gating_uses_canonical_nyse_holidays() -> None:
    # Good Friday, Juneteenth and observed Independence Day are all full
    # NYSE holidays in 2026.
    for closed_day in (
        datetime(2026, 4, 3, 9, 30, tzinfo=ET),
        datetime(2026, 6, 19, 9, 30, tzinfo=ET),
        datetime(2026, 7, 3, 9, 30, tzinfo=ET),
    ):
        info = get_session_info(closed_day)
        assert info.is_open_day is False
        assert detect_event(closed_day, tolerance_minutes=0) is None


def test_session_gating_honors_early_close() -> None:
    # Christmas Eve 2026 is a 13:00 ET close. Later intraday checkpoints
    # must be removed rather than emitted after the market has closed.
    close = datetime(2026, 12, 24, 13, 0, tzinfo=ET)
    after_close_checkpoint = datetime(2026, 12, 24, 13, 30, tzinfo=ET)
    info = get_session_info(close)
    assert info.is_open_day is True
    assert info.is_early_close is True
    assert detect_event(close, tolerance_minutes=0) == "CLOSE"
    assert detect_event(after_close_checkpoint, tolerance_minutes=0) is None
