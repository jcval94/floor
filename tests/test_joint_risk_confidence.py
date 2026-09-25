from __future__ import annotations

import pytest

from floor.pipeline.prediction_runtime import _horizon_confidence
from models.train_classic_horizons import (
    RISK_TARGET_JOINT_COVERAGE,
    _PreparedRow,
    _dummy_benchmark,
    _fit_risk_geometry,
    _risk_geometry_metrics,
)


def _row(
    floor_delta: float,
    ceiling_delta: float,
    *,
    atr: float = 0.02,
) -> _PreparedRow:
    return _PreparedRow(
        row={},
        close=100.0,
        floor_delta=floor_delta,
        ceiling_delta=ceiling_delta,
        features={"atr_14": atr},
    )


def test_joint_conformal_calibrates_pair_not_each_side_independently() -> None:
    rows = [
        _row(0.020, 0.020),
        _row(0.025, 0.022),
        _row(0.021, 0.027),
        _row(0.030, 0.024),
        _row(0.024, 0.031),
        _row(0.035, 0.026),
        _row(0.026, 0.036),
        _row(0.040, 0.028),
        _row(0.028, 0.041),
        _row(0.045, 0.045),
    ]
    floor_predictions = [0.020] * len(rows)
    ceiling_predictions = [0.020] * len(rows)

    risk = _fit_risk_geometry(rows, floor_predictions, ceiling_predictions)
    metrics = _risk_geometry_metrics(
        rows,
        floor_predictions,
        ceiling_predictions,
        risk,
    )

    assert risk["method"] == "joint_validation_conformal_max_residual"
    assert risk["target_joint_coverage"] == pytest.approx(
        RISK_TARGET_JOINT_COVERAGE
    )
    assert risk["target_marginal_coverage"] is None
    assert risk["floor_delta_addon"] == pytest.approx(
        risk["ceiling_delta_addon"]
    )
    assert metrics["risk_interval_coverage"] >= RISK_TARGET_JOINT_COVERAGE
    assert metrics["risk_joint_coverage_error"] == pytest.approx(
        abs(
            metrics["risk_interval_coverage"]
            - RISK_TARGET_JOINT_COVERAGE
        )
    )


def test_joint_risk_calibration_does_not_modify_central_predictions() -> None:
    rows = [_row(0.03, 0.04), _row(0.04, 0.05), _row(0.05, 0.06)]
    floor_predictions = [0.02, 0.03, 0.04]
    ceiling_predictions = [0.03, 0.04, 0.05]
    original_floor = list(floor_predictions)
    original_ceiling = list(ceiling_predictions)

    _fit_risk_geometry(rows, floor_predictions, ceiling_predictions)

    assert floor_predictions == original_floor
    assert ceiling_predictions == original_ceiling


def test_dummy_benchmark_is_train_only_and_reports_best_null_loss() -> None:
    train = [
        _row(0.01, 0.02, atr=0.01),
        _row(0.02, 0.04, atr=0.02),
        _row(0.03, 0.06, atr=0.03),
        _row(0.04, 0.08, atr=0.04),
    ]
    evaluation = [
        _row(0.015, 0.03, atr=0.015),
        _row(0.025, 0.05, atr=0.025),
    ]

    result = _dummy_benchmark(train, evaluation)

    assert result["selection_split"] == "validation"
    assert result["best_name"] in {"global_median", "atr_only"}
    assert result["best_mae_spread_pct"] == pytest.approx(
        min(
            result["global_median_mae_spread_pct"],
            result["atr_only_mae_spread_pct"],
        )
    )


def test_runtime_prefers_joint_risk_coverage_over_legacy_central_coverage() -> None:
    row = {
        "risk_empirical_joint_coverage_d1": 0.81,
        "breach_prob_d1": 0.78,
    }
    assert _horizon_confidence(row, "d1", 0.0) == pytest.approx(0.81)

    legacy = {"breach_prob_d1": 0.78}
    assert _horizon_confidence(legacy, "d1", 0.0) == pytest.approx(0.22)
