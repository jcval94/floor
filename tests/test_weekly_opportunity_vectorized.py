from __future__ import annotations

import math

import pytest

from models.train_weekly_opportunity import (
    FEATURE_NAMES,
    _fit_ridge,
    _fit_scaler,
    _scaled,
    _target,
    _usable,
)


def _row(idx: int) -> dict:
    close = 100.0 + 0.2 * idx
    floor = close * (0.94 + 0.0002 * (idx % 7))
    return {
        "close": close,
        "floor_q1": floor,
        "forward_return_q1": ((idx % 13) - 6) / 100.0,
        "split_eligible_q1": True,
        "momentum_20": math.sin(idx / 9.0) * 0.08,
        "rel_strength_20": math.cos(idx / 11.0) * 0.05,
        "trend_context_m3": math.sin(idx / 17.0) * 0.12,
        "drawdown_13w": -abs(math.cos(idx / 15.0)) * 0.20,
        "atr_14": 1.2 + (idx % 9) * 0.15,
        "price_position_in_range_20": (idx % 20) / 19.0,
    }


def _fit_ridge_scalar_reference(
    rows: list[dict],
    *,
    l2: float,
    lr: float,
    epochs: int,
) -> dict:
    usable = _usable(rows)
    means, scales = _fit_scaler(usable)
    weights = [0.0] * len(FEATURE_NAMES)
    targets = [float(_target(row) or 0.0) for row in usable]
    bias = sum(targets) / len(targets)
    n = float(len(usable))

    for _ in range(epochs):
        grad_w = [0.0] * len(FEATURE_NAMES)
        grad_b = 0.0
        for row, target in zip(usable, targets):
            x = _scaled(row, means, scales)
            pred = bias + sum(weight * value for weight, value in zip(weights, x))
            error = pred - target
            grad_b += 2.0 * error / n
            for j in range(len(weights)):
                grad_w[j] += 2.0 * error * x[j] / n
        for j in range(len(weights)):
            weights[j] -= lr * (grad_w[j] + 2.0 * l2 * weights[j])
        bias -= lr * grad_b

    return {
        "feature_means": means,
        "feature_scales": scales,
        "weights": weights,
        "bias": bias,
    }


def test_vectorized_fit_matches_frozen_scalar_contract() -> None:
    rows = [_row(idx) for idx in range(180)]
    expected = _fit_ridge_scalar_reference(rows, l2=0.03, lr=0.02, epochs=35)
    actual = _fit_ridge(rows, l2=0.03, lr=0.02, epochs=35)

    assert actual["feature_means"] == expected["feature_means"]
    assert actual["feature_scales"] == expected["feature_scales"]
    assert actual["weights"] == pytest.approx(expected["weights"], abs=1e-12, rel=1e-12)
    assert actual["bias"] == pytest.approx(expected["bias"], abs=1e-12, rel=1e-12)


def test_vectorized_fit_preserves_serialized_model_contract() -> None:
    params = _fit_ridge([_row(idx) for idx in range(60)], epochs=3)

    assert params["schema_version"] == 1
    assert params["model_type"] == "ridge_risk_adjusted_ranker"
    assert params["feature_names"] == list(FEATURE_NAMES)
    assert len(params["weights"]) == len(FEATURE_NAMES)
    assert params["l2"] == 0.03
    assert params["learning_rate"] == 0.02
    assert params["epochs"] == 3
    assert params["target_clip"] == 3.0
