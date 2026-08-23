from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from floor.config import RuntimeConfig
from floor.pipeline.intraday_cycle import maybe_build_order
from floor.schemas import SignalRecord


def _signal(action: str) -> SignalRecord:
    return SignalRecord(
        symbol="AAPL",
        as_of=datetime.now(tz=ZoneInfo("America/New_York")),
        horizon="d1",
        action=action,
        confidence=0.8,
        rationale="test",
    )


def test_runtime_defaults_to_non_live() -> None:
    assert RuntimeConfig().live_trading_enabled is False


def test_direct_legacy_order_creation_is_blocked_for_buy() -> None:
    with pytest.raises(RuntimeError, match="direct qty=1 order creation"):
        maybe_build_order(_signal("BUY"), RuntimeConfig())


def test_direct_legacy_order_creation_is_blocked_for_hold() -> None:
    with pytest.raises(RuntimeError, match="Legacy intraday cycle is disabled"):
        maybe_build_order(_signal("HOLD"), RuntimeConfig())
