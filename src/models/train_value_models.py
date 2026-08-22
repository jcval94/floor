from __future__ import annotations

from dataclasses import dataclass

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


FEATURE_NAMES = (
    "atr_ratio_14",
    "trend_context_m3",
    "drawdown_13w",
    "dist_to_low_3m",
)


def _to_float(value: object, default: float = 0.0) -> float:
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


def _fit_linear_delta(
    train_rows: list[dict],
    *,
    l2: float = 0.01,
    lr: float = 0.05,
    epochs: int = 240,
) -> tuple[dict[str, float], float]:
    rows = _usable(train_rows)
    if not rows:
        raise ValueError("No leakage-safe m3 rows with complete floor target")

    weights = {name: 0.0 for name in FEATURE_NAMES}
    targets = [_target_delta(row) for row in rows]
    y = [float(value) for value in targets if value is not None]
    bias = _mean(y)
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
            err = pred - target
            grad_bias += (2.0 / n) * err
            for name in FEATURE_NAMES:
                grad[name] += (2.0 / n) * err * feat[name]

        for name in FEATURE_NAMES:
            grad[name] += 2.0 * l2 * weights[name]
            weights[name] -= lr * grad[name]
        bias -= lr * grad_bias

    return weights, float(bias)


def predict_floor_delta(row: dict, weights: dict[str, float], bias: float) -> float:
    feat = _features(row)
    raw = bias + sum(
        _to_float(weights.get(name)) * feat[name]
        for name in FEATURE_NAMES
    )
    return max(0.0001, min(0.95, raw))


def _has_point_in_time_integrity_metadata(rows: list[dict]) -> bool:
    return any("target_end_date_m3" in row for row in rows)


def _expanding_time_folds(
    rows: list[dict],
    folds: int,
) -> list[tuple[list[dict], list[dict]]]:
    """Compatibility CV for synthetic fixtures only.

    Real ABT rows include target-end metadata. Until fold-specific purge logic is
    present, CV is deliberately disabled for those rows rather than leaking
    future labels across fold boundaries.
    """
    if _has_point_in_time_integrity_metadata(rows):
        return []

    valid_rows = _usable(rows)
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


def _score_delta_model(
    rows: list[dict],
    weights: dict[str, float],
    bias: float,
) -> float:
    errors: list[float] = []
    for row in _usable(rows):
        target = _target_delta(row)
        if target is None:
            continue
        errors.append(abs(predict_floor_delta(row, weights, bias) - target))
    return _mean(errors) if errors else float("inf")


def _select_hyperparameters_with_cv(
    train_rows: list[dict],
    folds: int = 3,
) -> tuple[float, float, dict]:
    folds_data = _expanding_time_folds(train_rows, folds=folds)
    if not folds_data:
        reason = (
            "disabled_to_prevent_fold_leakage"
            if _has_point_in_time_integrity_metadata(train_rows)
            else "insufficient_rows"
        )
        return 0.01, 0.05, {
            "cv_enabled": False,
            "reason": reason,
            "folds": 0,
            "grid_size": 0,
        }

    grid = [
        (0.001, 0.03),
        (0.01, 0.05),
        (0.05, 0.03),
    ]
    best = grid[0]
    best_score = float("inf")
    for l2, lr in grid:
        scores: list[float] = []
        for fold_train, fold_valid in folds_data:
            weights, bias = _fit_linear_delta(
                fold_train,
                l2=l2,
                lr=lr,
                epochs=180,
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
        "best_cv_score": round(best_score, 8),
    }


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

    l2 = 0.01
    lr = 0.05
    tuning_summary = {
        "cv_enabled": False,
        "folds": 0,
        "grid_size": 0,
    }
    if training_mode == "retrain":
        l2, lr, tuning_summary = _select_hyperparameters_with_cv(
            usable_train,
            folds=3,
        )

    weights, bias = _fit_linear_delta(
        usable_train,
        l2=l2,
        lr=lr,
        epochs=260,
    )

    valid_delta_raw = [
        predict_floor_delta(row, weights, bias)
        for row in usable_valid
    ]
    valid_delta_true = [
        float(_target_delta(row))
        for row in usable_valid
        if _target_delta(row) is not None
    ]

    calibrator = QuantileCalibrator(alpha=0.2).fit(
        valid_delta_raw,
        valid_delta_true,
    )
    calibrated_delta = [
        max(0.0001, min(0.95, value))
        for value in calibrator.transform(valid_delta_raw)
    ]

    predicted_floors = [
        _to_float(row.get("close")) * (1.0 - delta)
        for row, delta in zip(usable_valid, calibrated_delta)
    ]
    true_floors = [_to_float(row.get("floor_m3")) for row in usable_valid]
    confidences = [
        0.5
        + min(
            0.45,
            abs(_to_float(row.get("ai_conviction_long")) * 0.4),
        )
        for row in usable_valid
    ]

    metrics = value_metrics(true_floors, predicted_floors, confidences)
    metrics["target"] = "floor_m3"
    metrics["training_target"] = "floor_delta_m3"
    metrics["horizon"] = "m3"
    metrics["train_rows"] = len(usable_train)
    metrics["validation_rows"] = len(usable_valid)

    return ValueModelArtifact(
        model_name=model_name,
        horizon="m3",
        target="floor_m3",
        version=version,
        params={
            "schema_version": 2,
            "target_space": "relative_floor_delta",
            "features": list(FEATURE_NAMES),
            "weights": weights,
            "bias": bias,
            "calibration_scale": calibrator.scale,
            "delta_clip": [0.0001, 0.95],
            "l2": l2,
            "learning_rate": lr,
            "tuning_summary": tuning_summary,
            "hyperparameter_grid": {
                "l2": [0.001, 0.01, 0.05],
                "learning_rate": [0.03, 0.05],
            },
        },
        metrics=metrics,
        predictions=predicted_floors,
        confidences=confidences,
    )