from __future__ import annotations

from dataclasses import dataclass

from models.evaluate import pinball_loss, value_metrics
from models.temporal_cv import chronological_calibration_split, purged_expanding_folds


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


FEATURE_NAMES = (
    "atr_ratio_14",
    "trend_context_m3",
    "drawdown_13w",
    "dist_to_low_3m",
)
TARGET_BREACH_RATE = 0.20
TARGET_DELTA_QUANTILE = 1.0 - TARGET_BREACH_RATE


def _to_float(value: object, default: float = 0.0) -> float:
    if not isinstance(value, (int, float, str, bytes, bytearray)):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _eligible(row: dict) -> bool:
    return row.get("split_eligible_m3", True) is not False


def _target_delta(row: dict) -> float | None:
    direct = row.get("floor_delta_m3")
    if direct not in (None, ""):
        return max(0.0, min(0.95, _to_float(direct)))

    close = _to_float(row.get("close"))
    floor = row.get("floor_m3")
    if close <= 0 or floor in (None, ""):
        return None
    return max(0.0, min(0.95, (close - _to_float(floor)) / close))


def _features(row: dict) -> dict[str, float]:
    close = max(_to_float(row.get("close")), 1e-9)
    return {
        "atr_ratio_14": abs(_to_float(row.get("atr_14"))) / close,
        "trend_context_m3": _to_float(row.get("trend_context_m3")),
        "drawdown_13w": _to_float(row.get("drawdown_13w")),
        "dist_to_low_3m": _to_float(row.get("dist_to_low_3m")),
    }


def _usable(rows: list[dict]) -> list[dict]:
    return [
        row
        for row in rows
        if _eligible(row)
        and _target_delta(row) is not None
        and _to_float(row.get("close")) > 0
    ]


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    idx = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * q))))
    return ordered[idx]


def _fit_linear_delta(
    train_rows: list[dict],
    *,
    l2: float = 0.01,
    lr: float = 0.03,
    epochs: int = 320,
    quantile: float = TARGET_DELTA_QUANTILE,
) -> tuple[dict[str, float], float]:
    """Fit a linear conditional quantile of 65-session drawdown.

    A 20% desired floor-breach rate means the drawdown delta should target its
    80th conditional quantile. MSE estimates the mean and is therefore the wrong
    objective for a risk floor.
    """

    rows = _usable(train_rows)
    if not rows:
        raise ValueError("No leakage-safe m3 rows with complete floor target")

    weights = {name: 0.0 for name in FEATURE_NAMES}
    targets = [_target_delta(row) for row in rows]
    y = [value for value in targets if value is not None]
    bias = _quantile(y, quantile)
    n = float(len(rows))

    for _ in range(epochs):
        grad = {name: 0.0 for name in FEATURE_NAMES}
        grad_bias = 0.0
        for row in rows:
            target = _target_delta(row)
            if target is None:
                continue
            feat = _features(row)
            pred = bias + sum(weights[name] * feat[name] for name in FEATURE_NAMES)
            # d pinball(y - pred) / d pred
            derivative = -quantile if target > pred else (1.0 - quantile)
            grad_bias += derivative / n
            for name in FEATURE_NAMES:
                grad[name] += derivative * feat[name] / n

        for name in FEATURE_NAMES:
            grad[name] += 2.0 * l2 * weights[name]
            weights[name] -= lr * grad[name]
        bias -= lr * grad_bias

    return weights, float(bias)


def predict_floor_delta(row: dict, weights: dict[str, float], bias: float) -> float:
    feat = _features(row)
    raw = bias + sum(_to_float(weights.get(name)) * feat[name] for name in FEATURE_NAMES)
    return max(0.0001, min(0.95, raw))


def _has_point_in_time_integrity_metadata(rows: list[dict]) -> bool:
    return any("target_end_date_m3" in row for row in rows)


def _expanding_time_folds(
    rows: list[dict],
    folds: int,
) -> list[tuple[list[dict], list[dict]]]:
    valid_rows = _usable(rows)
    if _has_point_in_time_integrity_metadata(valid_rows):
        return purged_expanding_folds(
            valid_rows,
            target_end_field="target_end_date_m3",
            folds=folds,
            min_train_dates=20,
        )

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


def _score_delta_model(rows: list[dict], weights: dict[str, float], bias: float) -> float:
    true: list[float] = []
    predicted: list[float] = []
    for row in _usable(rows):
        target = _target_delta(row)
        if target is None:
            continue
        true.append(target)
        predicted.append(predict_floor_delta(row, weights, bias))
    return pinball_loss(true, predicted, alpha=TARGET_DELTA_QUANTILE) if true else float("inf")


def _select_hyperparameters_with_cv(
    train_rows: list[dict],
    folds: int = 3,
) -> tuple[float, float, dict]:
    folds_data = _expanding_time_folds(train_rows, folds=folds)
    if not folds_data:
        return 0.01, 0.03, {
            "cv_enabled": False,
            "reason": "insufficient_purged_temporal_folds",
            "folds": 0,
            "grid_size": 0,
        }

    grid = [(0.001, 0.02), (0.01, 0.03), (0.05, 0.02)]
    best = grid[0]
    best_score = float("inf")
    for l2, lr in grid:
        scores: list[float] = []
        for fold_train, fold_valid in folds_data:
            weights, bias = _fit_linear_delta(
                fold_train,
                l2=l2,
                lr=lr,
                epochs=240,
            )
            scores.append(_score_delta_model(fold_valid, weights, bias))
        score = _mean(scores)
        if score < best_score:
            best_score = score
            best = (l2, lr)

    return best[0], best[1], {
        "cv_enabled": True,
        "folds": len(folds_data),
        "grid_size": len(grid),
        "best_cv_pinball_delta": round(best_score, 8),
    }


def _calibration_scale(rows: list[dict], weights: dict[str, float], bias: float) -> float:
    ratios: list[float] = []
    for row in _usable(rows):
        target = _target_delta(row)
        raw = predict_floor_delta(row, weights, bias)
        if target is not None and raw > 1e-9:
            ratios.append(target / raw)
    # Choose the ratio quantile that corresponds to the desired 20% breach rate.
    return max(0.25, min(4.0, _quantile(ratios, TARGET_DELTA_QUANTILE) if ratios else 1.0))


def train_floor_m3_value_model(
    train_rows: list[dict],
    valid_rows: list[dict],
    model_name: str,
    version: str,
    training_mode: str = "standard",
) -> ValueModelArtifact:
    usable_train = _usable(train_rows)
    usable_valid = _usable(valid_rows)
    if not usable_train:
        raise ValueError("m3 value training requires complete leakage-safe horizons")
    if not usable_valid:
        raise ValueError("m3 value validation requires complete leakage-safe horizons")

    l2 = 0.01
    lr = 0.03
    tuning_summary = {"cv_enabled": False, "folds": 0, "grid_size": 0, "reason": "not_requested"}
    if training_mode == "retrain":
        l2, lr, tuning_summary = _select_hyperparameters_with_cv(usable_train, folds=3)

    weights, bias = _fit_linear_delta(usable_train, l2=l2, lr=lr, epochs=360)

    calibration_rows, evaluation_rows = chronological_calibration_split(usable_valid)
    scale = _calibration_scale(calibration_rows, weights, bias)
    evaluation_rows = _usable(evaluation_rows)
    if not evaluation_rows:
        evaluation_rows = usable_valid

    calibrated_delta = [
        max(0.0001, min(0.95, predict_floor_delta(row, weights, bias) * scale))
        for row in evaluation_rows
    ]
    true_delta = [float(_target_delta(row) or 0.0) for row in evaluation_rows]
    predicted_floors = [
        _to_float(row.get("close")) * (1.0 - delta)
        for row, delta in zip(evaluation_rows, calibrated_delta)
    ]
    true_floors = [_to_float(row.get("floor_m3")) for row in evaluation_rows]

    # Confidence now represents the actual modeled breach event, rather than an
    # unrelated AI conviction field. A calibrated 20% floor breach gets p=0.20.
    confidences = [TARGET_BREACH_RATE] * len(evaluation_rows)
    metrics = value_metrics(true_floors, predicted_floors, confidences)
    metrics.update(
        {
            "pinball_loss_delta": pinball_loss(
                true_delta,
                calibrated_delta,
                alpha=TARGET_DELTA_QUANTILE,
            ),
            "mae_delta": _mean([abs(t - p) for t, p in zip(true_delta, calibrated_delta)]),
            "target_breach_rate": TARGET_BREACH_RATE,
            "breach_rate_error": abs(float(metrics.get("breach_rate", 0.0)) - TARGET_BREACH_RATE),
            "target": "floor_m3",
            "training_target": "floor_delta_m3",
            "horizon": "m3",
            "train_rows": len(usable_train),
            "calibration_rows": len(calibration_rows),
            "validation_rows": len(evaluation_rows),
        }
    )

    return ValueModelArtifact(
        model_name=model_name,
        horizon="m3",
        target="floor_m3",
        version=version,
        params={
            "schema_version": 2,
            "target_space": "relative_floor_delta",
            "loss": "pinball_quantile",
            "target_delta_quantile": TARGET_DELTA_QUANTILE,
            "target_breach_rate": TARGET_BREACH_RATE,
            "features": list(FEATURE_NAMES),
            "weights": weights,
            "bias": bias,
            "calibration_scale": scale,
            "calibration_method": "chronological_holdout_quantile_ratio",
            "delta_clip": [0.0001, 0.95],
            "l2": l2,
            "learning_rate": lr,
            "tuning_summary": tuning_summary,
            "hyperparameter_grid": {
                "l2": [0.001, 0.01, 0.05],
                "learning_rate": [0.02, 0.03],
            },
        },
        metrics=metrics,
        predictions=predicted_floors,
        confidences=confidences,
    )
