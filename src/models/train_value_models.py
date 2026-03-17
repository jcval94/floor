from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import TypedDict

from models.calibration import QuantileCalibrator
from models.evaluate import value_metrics


@dataclass
class ValueModelArtifact:
    model_name: str
    horizon: str
    target: str
    version: str
    params: dict
    metrics: dict
    predictions: list[float]
    confidences: list[float]


class HyperparameterConfig(TypedDict):
    weights: dict[str, float]
    bias: float


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _linear_predict(row: dict, weights: dict[str, float], bias: float) -> float:
    return bias + sum(float(row.get(k, 0.0) or 0.0) * w for k, w in weights.items())


def _fit_baseline(train_rows: list[dict], target: str) -> tuple[dict[str, float], float]:
    y = [float(r[target]) for r in train_rows if r.get(target) is not None]
    bias = _mean(y)
    # Stable, leakage-safe hand-crafted weights aligned with current feature stack.
    return ({"atr_14": -0.6, "trend_context_m3": 0.8, "drawdown_13w": 0.4, "dist_to_low_3m": -0.5}, bias)


def _value_composite_score(metrics: dict) -> float:
    return (
        float(metrics.get("pinball_loss", 999.0))
        + float(metrics.get("mae_realized_floor", 999.0))
        + abs(float(metrics.get("breach_rate", 0.2)) - 0.2)
        + float(metrics.get("calibration_error", 999.0))
        + (1 - float(metrics.get("temporal_stability", 0.0)))
    )


def _expanding_time_folds(rows: list[dict], folds: int) -> list[tuple[list[dict], list[dict]]]:
    valid_rows = [r for r in rows if r.get("floor_m3") is not None]
    if len(valid_rows) < max(12, folds * 2):
        return []

    fold_size = max(1, len(valid_rows) // (folds + 1))
    result: list[tuple[list[dict], list[dict]]] = []
    for i in range(1, folds + 1):
        train_end = max(fold_size, i * fold_size)
        valid_end = min(len(valid_rows), train_end + fold_size)
        train = valid_rows[:train_end]
        valid = valid_rows[train_end:valid_end]
        if train and valid:
            result.append((train, valid))
    return result


def _hyperparameter_grid(base_weights: dict[str, float], base_bias: float) -> list[HyperparameterConfig]:
    atr_weights = [-0.8, -0.6, -0.4]
    trend_weights = [0.6, 0.8, 1.0]
    drawdown_weights = [0.2, 0.4, 0.6]
    dist_weights = [-0.7, -0.5, -0.3]
    bias_offsets = [-0.5, 0.0, 0.5]

    return [
        {
            "weights": {
                "atr_14": atr_w,
                "trend_context_m3": trend_w,
                "drawdown_13w": dd_w,
                "dist_to_low_3m": dist_w,
            },
            "bias": base_bias + b_off,
        }
        for atr_w, trend_w, dd_w, dist_w, b_off in product(
            atr_weights,
            trend_weights,
            drawdown_weights,
            dist_weights,
            bias_offsets,
        )
    ]


def _select_hyperparameters_with_cv(train_rows: list[dict], base_weights: dict[str, float], base_bias: float, folds: int = 3) -> tuple[dict[str, float], float, dict]:
    folds_data = _expanding_time_folds(train_rows, folds=folds)
    if not folds_data:
        return base_weights, base_bias, {"cv_enabled": False, "reason": "insufficient_rows", "folds": folds}

    grid = _hyperparameter_grid(base_weights, base_bias)
    best_weights = base_weights
    best_bias = base_bias
    best_score = float("inf")

    for config in grid:
        fold_scores: list[float] = []
        for _, fold_valid in folds_data:
            y_true = [float(r["floor_m3"]) for r in fold_valid if r.get("floor_m3") is not None]
            raw_pred = [_linear_predict(r, config["weights"], config["bias"]) for r in fold_valid if r.get("floor_m3") is not None]
            if not y_true:
                continue
            calibrator = QuantileCalibrator(alpha=0.2).fit(raw_pred, y_true)
            pred = calibrator.transform(raw_pred)
            confidences = [0.5 + min(0.45, abs(float(r.get("ai_conviction_long") or 0.0) * 0.4)) for r in fold_valid if r.get("floor_m3") is not None]
            metrics = value_metrics(y_true, pred, confidences)
            fold_scores.append(_value_composite_score(metrics))

        if not fold_scores:
            continue
        score = sum(fold_scores) / len(fold_scores)
        if score < best_score:
            best_score = score
            best_weights = config["weights"]
            best_bias = float(config["bias"])

    return (
        best_weights,
        best_bias,
        {
            "cv_enabled": True,
            "folds": len(folds_data),
            "grid_size": len(grid),
            "best_cv_score": round(best_score, 8) if best_score < float("inf") else None,
        },
    )


def train_floor_m3_value_model(
    train_rows: list[dict],
    valid_rows: list[dict],
    model_name: str,
    version: str,
    training_mode: str = "standard",
) -> ValueModelArtifact:
    target = "floor_m3"
    weights, bias = _fit_baseline(train_rows, target)

    tuning_summary = {"cv_enabled": False, "folds": 0, "grid_size": 0}
    if training_mode == "retrain":
        weights, bias, tuning_summary = _select_hyperparameters_with_cv(train_rows, weights, bias, folds=3)

    valid_y = [float(r[target]) for r in valid_rows if r.get(target) is not None]
    raw_pred = [_linear_predict(r, weights, bias) for r in valid_rows if r.get(target) is not None]

    calibrator = QuantileCalibrator(alpha=0.2).fit(raw_pred, valid_y)
    pred = calibrator.transform(raw_pred)
    confidences = [0.5 + min(0.45, abs(float(r.get("ai_conviction_long") or 0.0) * 0.4)) for r in valid_rows if r.get(target) is not None]

    metrics = value_metrics(valid_y, pred, confidences)
    metrics["target"] = target
    metrics["horizon"] = "m3"

    return ValueModelArtifact(
        model_name=model_name,
        horizon="m3",
        target=target,
        version=version,
        params={
            "weights": weights,
            "bias": bias,
            "calibration_scale": calibrator.scale,
            "tuning_summary": tuning_summary,
            "hyperparameter_grid": {
                "atr_14": [-0.8, -0.6, -0.4],
                "trend_context_m3": [0.6, 0.8, 1.0],
                "drawdown_13w": [0.2, 0.4, 0.6],
                "dist_to_low_3m": [-0.7, -0.5, -0.3],
                "bias_offset": [-0.5, 0.0, 0.5],
            },
        },
        metrics=metrics,
        predictions=pred,
        confidences=confidences,
    )
