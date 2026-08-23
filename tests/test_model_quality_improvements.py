from __future__ import annotations

from datetime import datetime, timedelta

from models.temporal_cv import purged_expanding_folds
from models.train_timing_models import train_floor_week_m3_timing_model
from models.train_value_models import train_floor_m3_value_model
from models.train_weekly_opportunity import (
    predict_weekly_opportunity,
    train_weekly_opportunity_model,
)


def _dated_rows(n: int = 120) -> list[dict]:
    start = datetime(2024, 1, 2, 16, 0)
    rows: list[dict] = []
    for i in range(n):
        ts = start + timedelta(days=i)
        end_m3 = ts + timedelta(days=70)
        end_q1 = ts + timedelta(days=14)
        close = 100.0 + 0.15 * i
        momentum = -0.08 + 0.0015 * i
        forward = -0.03 + 0.0007 * i
        floor_delta_q1 = 0.05 if i % 2 else 0.04
        floor_delta_m3 = 0.10 + 0.02 * (i % 5) / 4
        rows.append(
            {
                "symbol": "AAA",
                "timestamp": ts.isoformat(),
                "target_end_date_m3": end_m3.date().isoformat(),
                "target_end_date_q1": end_q1.date().isoformat(),
                "split_eligible_m3": True,
                "split_eligible_q1": True,
                "close": close,
                "atr_14": close * (0.015 + 0.00005 * i),
                "trend_context_m3": momentum * 0.5,
                "drawdown_13w": -0.12 + 0.0008 * i,
                "dist_to_low_3m": 0.04 + 0.001 * i,
                "momentum_20": momentum,
                "rel_strength_20": momentum * 0.7,
                "price_position_in_range_20": min(0.95, max(0.05, 0.4 + momentum)),
                "floor_delta_m3": floor_delta_m3,
                "floor_m3": close * (1.0 - floor_delta_m3),
                "floor_week_m3": (i % 13) + 1,
                "forward_return_q1": forward,
                "floor_q1": close * (1.0 - floor_delta_q1),
            }
        )
    return rows


def test_purged_folds_never_cross_validation_boundary() -> None:
    rows = _dated_rows(140)
    folds = purged_expanding_folds(
        rows,
        target_end_field="target_end_date_q1",
        folds=3,
        min_train_dates=20,
    )
    assert folds
    for train, valid in folds:
        valid_start = min(str(row["timestamp"])[:10] for row in valid)
        assert train
        assert all(str(row["target_end_date_q1"]) < valid_start for row in train)


def test_m3_value_uses_quantile_loss_and_scale_free_quality_metrics() -> None:
    rows = _dated_rows(120)
    artifact = train_floor_m3_value_model(rows[:80], rows[80:], "m3_value_quantile", "v3", training_mode="retrain")
    assert artifact.params["loss"] == "pinball_quantile"
    assert artifact.params["target_breach_rate"] == 0.20
    assert artifact.params["calibration_method"] == "chronological_holdout_quantile_ratio"
    assert "pinball_loss_delta" in artifact.metrics
    assert "mae_delta" in artifact.metrics
    assert "breach_rate_error" in artifact.metrics
    assert artifact.metrics["calibration_rows"] > 0
    assert artifact.metrics["validation_rows"] > 0


def test_m3_timing_uses_ordinal_objective_and_out_of_time_temperature() -> None:
    rows = _dated_rows(120)
    artifact = train_floor_week_m3_timing_model(rows[:80], rows[80:], "m3_timing_ordinal", "v3", training_mode="retrain")
    assert artifact.params["objective"] == "ordinal_neighbor_smoothed_cross_entropy"
    assert artifact.params["calibration_method"] == "chronological_holdout_temperature"
    assert 0.25 <= float(artifact.params["temperature"]) <= 4.0
    assert "log_loss_skill" in artifact.metrics
    assert "abstention_rate" in artifact.metrics
    assert artifact.metrics["calibration_rows"] > 0
    assert artifact.metrics["validation_rows"] > 0


def test_weekly_opportunity_challenger_is_risk_adjusted_and_not_canonical() -> None:
    rows = _dated_rows(150)
    artifact = train_weekly_opportunity_model(rows[:100], rows[100:], version="v1", tune=True)
    assert artifact.horizon == "q1"
    assert artifact.target == "risk_adjusted_opportunity_q1"
    assert artifact.params["canonical_serving_enabled"] is False
    assert artifact.metrics["validation_rows"] == len(artifact.predictions)
    assert "top_quintile_return_lift" in artifact.metrics

    low = dict(rows[110])
    high = dict(rows[140])
    assert predict_weekly_opportunity(high, artifact.params) > predict_weekly_opportunity(low, artifact.params)
