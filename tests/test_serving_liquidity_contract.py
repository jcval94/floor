from __future__ import annotations

from statistics import mean

import pytest

from floor.pipeline.prediction_runtime import (
    MODEL_INPUT_FIELDS,
    _strategy_liquidity_snapshot,
)
from strategies.common.mechanics import liquidity_ok


def test_serving_strategy_liquidity_uses_exact_trailing_20_dollar_volume() -> None:
    rows: list[dict] = []
    for index in range(25):
        close = 100.0 + index
        volume = 1_000_000.0 + index * 25_000.0
        rows.append(
            {
                "symbol": "AAA",
                "timestamp": f"2026-01-{index + 1:02d}T21:00:00+00:00",
                "close": close,
                "volume": volume,
            }
        )

    payload = _strategy_liquidity_snapshot(rows)["AAA"]
    expected = mean(
        float(row["close"]) * float(row["volume"]) for row in rows[-20:]
    )

    assert payload["dollar_volume"] == pytest.approx(
        float(rows[-1]["close"]) * float(rows[-1]["volume"])
    )
    assert payload["avg_dollar_volume"] == pytest.approx(expected)
    assert payload["avg_dollar_volume_observations"] == 20.0
    assert liquidity_ok(
        payload,
        {"liquidity": {"min_avg_dollar_volume": expected * 0.9}},
    )
    assert not liquidity_ok(
        payload,
        {"liquidity": {"min_avg_dollar_volume": expected * 1.1}},
    )


def test_liquidity_fields_do_not_enter_forecasting_model_input_contract() -> None:
    assert "dollar_volume" not in MODEL_INPUT_FIELDS
    assert "avg_dollar_volume" not in MODEL_INPUT_FIELDS


def test_liquidity_snapshot_is_symbol_scoped() -> None:
    rows = [
        {"symbol": "AAA", "timestamp": "2026-01-01", "close": 100.0, "volume": 10.0},
        {"symbol": "BBB", "timestamp": "2026-01-01", "close": 20.0, "volume": 50.0},
        {"symbol": "AAA", "timestamp": "2026-01-02", "close": 110.0, "volume": 20.0},
        {"symbol": "BBB", "timestamp": "2026-01-02", "close": 25.0, "volume": 60.0},
    ]

    payload = _strategy_liquidity_snapshot(rows)

    assert payload["AAA"]["avg_dollar_volume"] == pytest.approx((1000.0 + 2200.0) / 2)
    assert payload["BBB"]["avg_dollar_volume"] == pytest.approx((1000.0 + 1500.0) / 2)