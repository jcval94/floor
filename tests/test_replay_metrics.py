from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from replay.runner import _evaluate_predictions

ET = ZoneInfo("America/New_York")


def _bar(symbol: str, day: date, low: float, high: float) -> dict:
    return {
        "symbol": symbol,
        "timestamp": datetime(
            day.year, day.month, day.day, 9, 30, tzinfo=ET
        ).isoformat(),
        "open": (low + high) / 2,
        "high": high,
        "low": low,
        "close": (low + high) / 2,
        "volume": 1000.0,
    }


def test_evaluation_resolves_only_mature_horizons() -> None:
    daily = {
        "AAA": [
            _bar("AAA", date(2026, 8, 24), 99.0, 101.0),
            _bar("AAA", date(2026, 8, 25), 98.0, 104.0),
            _bar("AAA", date(2026, 8, 26), 97.0, 105.0),
            _bar("AAA", date(2026, 8, 27), 96.0, 106.0),
            _bar("AAA", date(2026, 8, 28), 95.0, 107.0),
            _bar("AAA", date(2026, 8, 31), 94.0, 108.0),
        ]
    }
    as_of = datetime(2026, 8, 24, 16, 0, tzinfo=ET).isoformat()
    predictions = [
        {
            "symbol": "AAA",
            "as_of": as_of,
            "event_type": "CLOSE",
            "horizon": "d1",
            "floor_value": 97.0,
            "ceiling_value": 105.0,
        },
        {
            "symbol": "AAA",
            "as_of": as_of,
            "event_type": "CLOSE",
            "horizon": "w1",
            "floor_value": 93.0,
            "ceiling_value": 109.0,
        },
        {
            "symbol": "AAA",
            "as_of": as_of,
            "event_type": "CLOSE",
            "horizon": "q1",
            "floor_value": 90.0,
            "ceiling_value": 110.0,
        },
    ]

    result = _evaluate_predictions(predictions, daily)
    summary = result["summary"]["by_horizon"]

    assert summary["d1"]["resolved"] == 1
    assert summary["w1"]["resolved"] == 1
    assert summary["q1"]["resolved"] == 0
    assert summary["q1"]["pending"] == 1
    assert summary["d1"]["range_coverage_rate"] == 1.0
