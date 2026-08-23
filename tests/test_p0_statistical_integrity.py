from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from features.labels import build_labels
from features.run_features import assign_split
from models.horizon_timing import fit_horizon_timing, predict_horizon_timing
from forecasting.parity_models import ParityChampionModelSet
from models.inference import predict_timing_week_probabilities, predict_value_floor_m3
from models.train_timing_models import train_floor_week_m3_timing_model
from models.train_value_models import train_floor_m3_value_model


def _daily_rows(days: int, symbol: str = "AAA", scale: float = 100.0) -> list[dict]:
    start = datetime(2025, 1, 2, 16, 0)
    rows: list[dict] = []
    day = start
    produced = 0
    while produced < days:
        if day.weekday() < 5:
            close = scale * (1.0 + 0.001 * produced)
            rows.append(
                {
                    "symbol": symbol,
                    "timestamp": day.isoformat(),
                    "open": close * 0.998,
                    "high": close * 1.01,
                    "low": close * 0.99,
                    "close": close,
                    "volume": 1_000_000 + produced,
                    "atr_14": close * 0.015,
                    "trend_context_m3": 0.02 if produced % 2 else -0.02,
                    "drawdown_13w": -0.04,
                    "dist_to_low_3m": 0.08,
                    "momentum_20": 0.03,
                }
            )
            produced += 1
        day += timedelta(days=1)
    return rows


def test_labels_require_complete_future_horizon_and_mark_censoring() -> None:
    labeled = build_labels(_daily_rows(66))
    assert labeled[0]["horizon_complete_m3"] is True
    assert labeled[0]["floor_m3"] is not None
    assert labeled[0]["floor_delta_m3"] is not None
    assert labeled[0]["target_end_date_m3"] is not None
    assert labeled[1]["horizon_complete_m3"] is False
    assert labeled[1]["floor_m3"] is None
    assert labeled[1]["floor_week_m3"] is None
    assert labeled[1]["target_end_date_m3"] is None


def test_daily_bars_do_not_fabricate_d1_intraday_timing() -> None:
    labeled = build_labels(_daily_rows(3))
    first = labeled[0]
    assert first["floor_d1"] is not None
    assert first["ceiling_d1"] is not None
    assert first["d1_timing_available"] is False
    assert first["floor_time_bucket_d1"] is None
    assert first["ceiling_time_bucket_d1"] is None


def test_split_eligibility_purges_targets_that_cross_boundary() -> None:
    labeled = assign_split(build_labels(_daily_rows(120)))
    train = [row for row in labeled if row["split"] == "train"]
    assert any(row["split_eligible_q1"] is True for row in train)
    assert any(
        row["split_eligible_q1"] is False
        and row["split_ineligible_reason_q1"] == "target_crosses_split_boundary"
        for row in train
    )
    for row in train:
        if row["split_eligible_q1"]:
            assert row["target_end_date_q1"] <= train[-1]["timestamp"][:10]


def _timing_rows(horizon: str) -> list[dict]:
    rows = []
    upper = 5 if horizon == "w1" else 10
    floor_col = f"floor_day_{horizon}"
    ceiling_col = f"ceiling_day_{horizon}"
    for i in range(80):
        label = (i % upper) + 1
        rows.append(
            {
                "close": 100.0,
                "atr_14": 0.5 + (i % 3),
                "trend_context_m3": -0.2 if i % 2 else 0.2,
                floor_col: label,
                ceiling_col: upper - label + 1,
                f"split_eligible_{horizon}": True,
            }
        )
    return rows


@pytest.mark.parametrize("horizon,upper", [("w1", 5), ("q1", 10)])
def test_classic_timing_is_trained_and_never_leaves_horizon_domain(horizon: str, upper: int) -> None:
    params = fit_horizon_timing(_timing_rows(horizon), horizon)
    assert params["status"] == "trained"
    floor, _ = predict_horizon_timing(_timing_rows(horizon)[0], params, horizon, "floor")
    ceiling, _ = predict_horizon_timing(_timing_rows(horizon)[1], params, horizon, "ceiling")
    assert floor is not None and 1 <= int(floor) <= upper
    assert ceiling is not None and 1 <= int(ceiling) <= upper


def test_daily_d1_timing_artifact_is_explicitly_unavailable() -> None:
    rows = [
        {
            "floor_time_bucket_d1": None,
            "ceiling_time_bucket_d1": None,
            "split_eligible_d1": True,
            "close": 100.0,
            "atr_14": 2.0,
        }
    ]
    params = fit_horizon_timing(rows, "d1")
    assert params["status"] == "unavailable_daily_resolution"
    value, probability = predict_horizon_timing(rows[0], params, "d1", "floor")
    assert value is None
    assert probability == 0.0


def _m3_rows(n: int, close_scale: float = 1.0) -> list[dict]:
    rows = []
    for i in range(n):
        close = close_scale * (100.0 + i * 0.2)
        floor_delta = 0.08 + 0.015 * ((i % 5) / 4)
        rows.append(
            {
                "split_eligible_m3": True,
                "close": close,
                "floor_m3": close * (1.0 - floor_delta),
                "floor_delta_m3": floor_delta,
                "floor_week_m3": 1 if i < n // 2 else 13,
                "atr_14": close * (0.01 if i < n // 2 else 0.04),
                "trend_context_m3": -0.25 if i < n // 2 else 0.25,
                "drawdown_13w": -0.25 if i < n // 2 else -0.01,
                "dist_to_low_3m": 0.03 if i < n // 2 else 0.25,
                "momentum_20": -0.2 if i < n // 2 else 0.2,
                "ai_conviction_long": 0.0,
            }
        )
    return rows


def test_m3_value_trains_relative_target_not_cross_ticker_dollar_price() -> None:
    train = _m3_rows(80, 1.0) + _m3_rows(80, 10.0)
    valid = _m3_rows(20, 1.0) + _m3_rows(20, 10.0)
    artifact = train_floor_m3_value_model(train, valid, "m3_value_relative", "v2")
    assert artifact.params["schema_version"] == 2
    assert artifact.params["target_space"] == "relative_floor_delta"
    assert 0.0 < float(artifact.params["bias"]) < 1.0

    payload = {"params": artifact.params}
    low_scale = predict_value_floor_m3(valid[0], payload)
    high_scale = predict_value_floor_m3(valid[20], payload)
    low_delta = 1.0 - low_scale / valid[0]["close"]
    high_delta = 1.0 - high_scale / valid[20]["close"]
    assert abs(low_delta - high_delta) < 0.03


def test_m3_timing_model_probabilities_change_with_market_state() -> None:
    train = _m3_rows(120)
    valid = _m3_rows(30)
    artifact = train_floor_week_m3_timing_model(train, valid, "m3_timing_multiclass", "v2")
    first = train[0]
    last = train[-1]
    p_first = predict_timing_week_probabilities(first, {"params": artifact.params})
    p_last = predict_timing_week_probabilities(last, {"params": artifact.params})
    assert len(p_first) == len(p_last) == 13
    assert abs(sum(p_first) - 1.0) < 1e-9
    assert abs(sum(p_last) - 1.0) < 1e-9
    assert p_first != p_last
    assert p_first[0] > p_first[12]
    assert p_last[12] > p_last[0]


def test_canonical_parity_model_rejects_old_m3_artifacts_until_retrained() -> None:
    row = _m3_rows(1)[0]
    model = object.__new__(ParityChampionModelSet)
    model._value_champion = {"params": {"weights": {}, "bias": 250.0}}
    model._timing_champion = {"params": {"score_config": {}}}
    with pytest.raises(ValueError, match="deprecated absolute target schema"):
        model.predict_m3(row)