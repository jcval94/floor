from datetime import datetime
from zoneinfo import ZoneInfo

from floor.config import RuntimeConfig
from floor.pipeline.intraday_cycle import maybe_build_order
from floor.schemas import SignalRecord


def test_paper_trading_default():
    signal = SignalRecord(
        symbol="AAPL",
        as_of=datetime.now(tz=ZoneInfo("America/New_York")),
        horizon="d1",
        action="BUY",
        confidence=0.8,
        rationale="test",
    )
    order = maybe_build_order(signal, RuntimeConfig())
    assert order is not None
    assert order.mode == "PAPER"


def test_hold_generates_no_order():
    signal = SignalRecord(
        symbol="AAPL",
        as_of=datetime.now(tz=ZoneInfo("America/New_York")),
        horizon="d1",
        action="HOLD",
        confidence=0.8,
        rationale="test",
    )
    order = maybe_build_order(signal, RuntimeConfig())
    assert order is None
