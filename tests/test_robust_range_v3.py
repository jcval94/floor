from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pytest
from sklearn.ensemble import HistGradientBoostingRegressor

from models.classic_horizon_predictor import (
    build_runtime_features,
    model_family,
    predict_family_delta,
)
from models.robust_range_v3 import (
    export_hist_gradient_boosting,
    fit_floor_head,
    predict_head,
    validate_head,
)


@dataclass
class _FloorRow:
    floor_delta: float
    features: dict[str, float]


def test_atr_median_floor_is_scale_free_and_serializable() -> None:
    rows = [
        _FloorRow(0.02, {"atr_14": 0.01}),
        _FloorRow(0.03, {"atr_14": 0.01}),
        _FloorRow(0.08, {"atr_14": 0.02}),
    ]
    params = fit_floor_head(rows)

    assert params["multiplier"] == 3.0
    assert predict_head(params, {"atr_14": 0.015}) == pytest.approx(0.045)
    assert model_family("robust_range_v3_d1") == "robust_range_v3"
    assert predict_family_delta("robust_range_v3", params, {"atr_14": 0.015}) == pytest.approx(0.045)


def test_histogram_tree_export_matches_sklearn_prediction() -> None:
    rng = np.random.default_rng(42)
    matrix = rng.normal(size=(240, 3))
    target = 0.03 + 0.006 * matrix[:, 0] - 0.003 * matrix[:, 1]
    model = HistGradientBoostingRegressor(
        loss="absolute_error",
        learning_rate=0.05,
        max_iter=12,
        max_leaf_nodes=5,
        min_samples_leaf=10,
        early_stopping=False,
        random_state=102,
    ).fit(matrix, target)
    params = export_hist_gradient_boosting(model, ("a", "b", "c"))

    for vector, expected in zip(matrix[:20], model.predict(matrix[:20])):
        features = {name: float(value) for name, value in zip(("a", "b", "c"), vector)}
        assert predict_head(params, features) == pytest.approx(float(expected), abs=1e-12)


def test_runtime_features_match_training_normalization_and_calendar() -> None:
    features = build_runtime_features(
        {
            "timestamp": "2026-04-17T20:00:00+00:00",
            "open": 99.0,
            "high": 103.0,
            "low": 98.0,
            "close": 100.0,
            "atr_14": 2.5,
        }
    )

    assert features["atr_14"] == 0.025
    assert features["open_to_close"] == pytest.approx(-0.01)
    assert features["high_to_close"] == pytest.approx(0.03)
    assert features["low_to_close"] == pytest.approx(-0.02)
    assert features["month_sin"] == pytest.approx(3**0.5 / 2)
    assert features["month_cos"] == pytest.approx(-0.5)


def test_malformed_histogram_tree_fails_closed() -> None:
    params = {
        "kind": "hist_gradient_boosting",
        "features": ["atr_14"],
        "baseline": 0.02,
        "trees": [[[0, 3, 0.1, 1, 2, 0.0]]],
    }
    with pytest.raises(ValueError, match="feature index"):
        validate_head(params)
