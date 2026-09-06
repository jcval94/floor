from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from replay.point_in_time import (
    aggregate_partial_bar,
    build_point_in_time_feature_rows,
)

ET = ZoneInfo("America/New_York")


def _daily(symbol: str, start: date, count: int, base: float) -> list[dict]:
    rows = []
    day = start
    added = 0
    while added < count:
        if day.weekday() < 5:
            price = base + added * 0.1
            rows.append(
                {
                    "symbol": symbol,
                    "timestamp": datetime(
                        day.year, day.month, day.day, 9, 30, tzinfo=ET
                    ).isoformat(),
                    "open": price,
                    "high": price + 1.0,
                    "low": price - 1.0,
                    "close": price + 0.2,
                    "volume": 1_000_000 + added,
                }
            )
            added += 1
        day += timedelta(days=1)
    return rows


def _intraday(symbol: str, day: date, opens: list[tuple[int, int, float]]) -> list[dict]:
    rows = []
    for hour, minute, price in opens:
        rows.append(
            {
                "symbol": symbol,
                "timestamp": datetime(
                    day.year, day.month, day.day, hour, minute, tzinfo=ET
                ).isoformat(),
                "open": price,
                "high": price + 0.4,
                "low": price - 0.3,
                "close": price + 0.1,
                "volume": 1000.0,
            }
        )
    return rows


def test_open_uses_only_opening_print() -> None:
    day = date(2026, 8, 24)
    rows = _intraday("AAA", day, [(9, 30, 100.0), (9, 35, 101.0)])
    checkpoint = datetime(2026, 8, 24, 9, 30, tzinfo=ET)

    bar = aggregate_partial_bar(rows, day, checkpoint)

    assert bar["open"] == 100.0
    assert bar["high"] == 100.0
    assert bar["low"] == 100.0
    assert bar["close"] == 100.0
    assert bar["volume"] == 0.0
    assert bar["source_max_timestamp"] is None


def test_checkpoint_excludes_bar_starting_at_checkpoint() -> None:
    day = date(2026, 8, 24)
    rows = _intraday(
        "AAA",
        day,
        [
            (9, 30, 100.0),
            (11, 25, 105.0),
            (11, 30, 999.0),
        ],
    )
    checkpoint = datetime(2026, 8, 24, 11, 30, tzinfo=ET)

    bar = aggregate_partial_bar(rows, day, checkpoint)

    assert bar["high"] < 999.0
    assert bar["close"] == 105.1
    assert str(bar["source_max_timestamp"]).startswith("2026-08-24T11:25")


def test_feature_snapshot_uses_prior_daily_history_plus_partial_day() -> None:
    day = date(2026, 8, 24)
    daily = {
        "AAA": _daily("AAA", date(2026, 5, 1), 80, 100.0),
        "SPY": _daily("SPY", date(2026, 5, 1), 80, 400.0),
    }
    daily = {
        symbol: [row for row in rows if row["timestamp"][:10] < day.isoformat()]
        for symbol, rows in daily.items()
    }
    intraday = {
        "AAA": _intraday("AAA", day, [(9, 30, 130.0), (11, 25, 131.0)]),
        "SPY": _intraday("SPY", day, [(9, 30, 430.0), (11, 25, 431.0)]),
    }

    rows, audit = build_point_in_time_feature_rows(
        daily_by_symbol=daily,
        intraday_by_symbol=intraday,
        symbols=["AAA"],
        benchmark_symbol="SPY",
        session_day=day,
        event="OPEN_PLUS_2H",
    )

    assert len(rows) == 1
    assert rows[0]["timestamp"].startswith("2026-08-24T11:30")
    assert rows[0]["close"] == 131.1
    assert audit["future_data_used"] is False
    source = audit["source_max_timestamp_by_symbol"]["AAA"]
    assert source is not None
    assert datetime.fromisoformat(source).astimezone(ET) < datetime(
        2026, 8, 24, 11, 30, tzinfo=ET
    )
