from datetime import datetime
from zoneinfo import ZoneInfo

from utils.market_session import get_session_info


def test_session_info_weekend_closed():
    dt = datetime(2025, 7, 5, 12, 0, tzinfo=ZoneInfo("America/New_York"))
    info = get_session_info(dt)
    assert info.is_open_day is False


def test_session_info_weekday_open_day():
    dt = datetime(2025, 7, 7, 12, 0, tzinfo=ZoneInfo("America/New_York"))
    info = get_session_info(dt)
    assert info.is_open_day is True
