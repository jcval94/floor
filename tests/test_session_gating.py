from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from utils.market_session import detect_event


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
