from __future__ import annotations

from dataclasses import dataclass
import math

from models.calibration import ProbabilityCalibrator
from models.evaluate import timing_metrics, top3_weeks


FEATURE_NAMES = (
    "atr_ratio_14",
    "trend_context_m3",
    "drawdown_13w",
    "dist_to_low_3m",
    "momentum_20",
)
N_CLASSES = 13


@dataclass
class TimingModelArtifact:
    model_name: str
    horizon: str
    target: str
    version: str
    params: dict
    metrics: dict
    probabilities: list[list[float]]
    best_class: list[int]
    top3: list[list[dict]]


def _to_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _eligible_rows(rows: list[dict]) -> list[dict]:
    return [
        row
        for row in rows
        if row.get("split_eligible_m3", True) is not False
        and row.get("floor_week_m3") not in (None, "")
        and 1 <= int(row["floor_week_m3"]) <= N_CLASSES
    ]


def _feature_vector(row: dict) -> list[float]:
    close = max(_to_float(row.get("close"), 0.0), 1e-9)
    return [
        abs(_to_float(row.get("atr_14"), 0.0)) / close,
        _to_float(row.get("trend_context_m3"), 0.0),
        _to_float(row.get("drawdown_13w"), 0.0),
        _to_float(row.get("dist_to_low_3m"), 0.0),
        _to_float(row.get("momentum_20"), 0.0),
    ]


def _fit_scaler(rows: list[dict]) -> tuple[list[float], list[float]]:
    vectors = [_feature_vector(row) for row in rows]
    if not vectors:
        return [0.0] * len(FEATURE_NAMES), [1.0] * len(FEATURE_NAMES)
    means = [sum(v[j] for v in vectors) / len(vectors) for j in range(len(FEATURE_NAMES))]
    scales: list[float] = []
    for j, mean in enumerate(means):
        variance = sum((v[j] - mean) ** 2 for v in vectors) / max(1, len(vectors))
        scales.append(max(math.sqrt(variance), 1e-6))
    return means, scales


def _scaled_vector(row: dict, means: list[float], scales: list[float]) -> list[float]:
    raw = _feature_vector(row)
    return [(raw[j] - means[j]) / scales[j] for j in range(len(raw))]


def _softmax(logits: list[float]) -> list[float]:
    if not logits:
        return []
    max_logit = max(logits)
    exps = [math.exp(max(-50.0, min(50.0, value - max_logit))) for value in logits]
    total = sum(exps) or 1.0
    return [value / total for value in exps]


def _fit_multinomial(
    rows: list[dict],
    *,
    learning_rate: float = 0.05,
    l2: float = 0.01,
    epochs: int = 120,
) -> dict:
    eligible = _eligible_rows(rows)
    if not eligible:
        raise ValueError("No eligible m3 timing rows")

    means, scales = _fit_scaler(eligible)
    x = [_scaled_vector(row, means, scales) for row in eligible]
    y = [int(row["floor_week_m3"]) - 1 for row in eligible]
    n = float(len(eligible))
    p = len(FEATURE_NAMES)

    counts = [1.0] * N_CLASSES
    for target in y:
        counts[target] += 1.0
    count_total = sum(counts)
    bias = [math.log(count / count_total) for count in counts]
    weights = [[0.0] * p for _ in range(N_CLASSES)]

    for _ in range(epochs):
        grad_w = [[0.0] * p for _ in range(N_CLASSES)]
        grad_b = [0.0] * N_CLASSES
        for vector, target in zip(x, y):
            logits = [bias[k] + sum(weights[k][j] * vector[j] for j in range(p)) for k in range(N_CLASSES)]
            probs = _softmax(logits)
            for k in range(N_CLASSES):
                err = probs[k] - (1.0 if k == target else 0.0)
                grad_b[k] += err / n
                for j in range(p):
                    grad_w[k][j] += err * vector[j] / n
        for k in range(N_CLASSES):
            bias[k] -= learning_rate * grad_b[k]
            for j in range(p):
                grad = grad_w[k][j] + l2 * weights[k][j]
                weights[k][j] -= learning_rate * grad

    return {
        "schema_version": 2,
        "model_type": "multinomial_logistic",
        "class_count": N_CLASSES,
        "feature_names": list(FEATURE_NAMES),
        "feature_means": {name: means[i] for i, name in enumerate(FEATURE_NAMES)},
        "feature_scales": {name: scales[i] for i, name in enumerate(FEATURE_NAMES)},
        "weights": weights,
        "bias": bias,
        "learning_rate": learning_rate,
        "l2": l2,
        "epochs": epochs,
        "train_rows": len(eligible),
    }


def _validate_params(params: dict) -> None:
    if int(params.get("schema_version") or 0) != 2:
        raise ValueError("m3 timing champion uses deprecated schema; retrain required")
    if params.get("model_type") != "multinomial_logistic":
        raise ValueError("m3 timing champion has unsupported model_type")
    if int(params.get("class_count") or 0) != N_CLASSES:
        raise ValueError("m3 timing champion must have 13 classes")
    names = params.get("feature_names")
    weights = params.get("weights")
    bias = params.get("bias")
    if names != list(FEATURE_NAMES):
        raise ValueError("m3 timing champion feature contract mismatch")
    if not isinstance(weights, list) or len(weights) != N_CLASSES:
        raise ValueError("m3 timing champion weights must contain 13 rows")
    if not all(isinstance(row, list) and len(row) == len(FEATURE_NAMES) for row in weights):
        raise ValueError("m3 timing champion weight shape mismatch")
    if not isinstance(bias, list) or len(bias) != N_CLASSES:
        raise ValueError("m3 timing champion bias must contain 13 values")


def predict_week_probabilities(row: dict, params: dict, *, apply_calibration: bool = True) -> list[float]:
    _validate_params(params)
    means_map = params.get("feature_means") or {}
    scales_map = params.get("feature_scales") or {}
    means = [_to_float(means_map.get(name), 0.0) for name in FEATURE_NAMES]
    scales = [max(abs(_to_float(scales_map.get(name), 1.0)), 1e-6) for name in FEATURE_NAMES]
    vector = _scaled_vector(row, means, scales)
    weights = params["weights"]
    bias = params["bias"]
    logits = [
        _to_float(bias[k])
        + sum(_to_float(weights[k][j]) * vector[j] for j in range(len(FEATURE_NAMES)))
        for k in range(N_CLASSES)
    ]
    probs = _softmax(logits)

    reliability = params.get("calibrator_reliability")
    if apply_calibration and isinstance(reliability, dict) and reliability:
        calibrator = ProbabilityCalibrator(bins=10)
        calibrator.reliability = {int(k): _to_float(v) for k, v in reliability.items()}
        probs = calibrator.calibrate(probs)
    total = sum(probs)
    if total <= 0:
        return [1.0 / N_CLASSES] * N_CLASSES
    return [max(0.0, value) / total for value in probs]


def _cross_entropy(rows: list[dict], params: dict) -> float:
    eligible = _eligible_rows(rows)
    if not eligible:
        return float("inf")
    losses = []
    for row in eligible:
        probs = predict_week_probabilities(row, params, apply_calibration=False)
        target = int(row["floor_week_m3"]) - 1
        losses.append(-math.log(max(1e-12, probs[target])))
    return sum(losses) / len(losses)


def _has_integrity_metadata(rows: list[dict]) -> bool:
    return any("target_end_date_m3" in row for row in rows)


def _expanding_time_folds(rows: list[dict], folds: int) -> list[tuple[list[dict], list[dict]]]:
    eligible = _eligible_rows(rows)
    if _has_integrity_metadata(eligible):
        return []
    if len(eligible) < max(26, folds * 4):
        return []
    fold_size = max(1, len(eligible) // (folds + 1))
    result: list[tuple[list[dict], list[dict]]] = []
    for i in range(1, folds + 1):
        train_end = max(fold_size, i * fold_size)
        valid_end = min(len(eligible), train_end + fold_size)
        train = eligible[:train_end]
        valid = eligible[train_end:valid_end]
        if train and valid:
            result.append((train, valid))
    return result


def _select_hyperparameters_with_cv(train_rows: list[dict], folds: int = 3) -> tuple[dict, dict]:
    folds_data = _expanding_time_folds(train_rows, folds=folds)
    default = {"learning_rate": 0.05, "l2": 0.01, "epochs": 120}
    if not folds_data:
        reason = "disabled_to_prevent_fold_leakage" if _has_integrity_metadata(train_rows) else "insufficient_rows"
        params = _fit_multinomial(train_rows, **default)
        return params, {"cv_enabled": False, "reason": reason, "folds": 0, "grid_size": 0}

    grid = [
        {"learning_rate": 0.08, "l2": 0.005, "epochs": 100},
        {"learning_rate": 0.05, "l2": 0.01, "epochs": 120},
        {"learning_rate": 0.03, "l2": 0.03, "epochs": 140},
    ]
    best = grid[0]
    best_score = float("inf")
    for config in grid:
        scores = []
        for fold_train, fold_valid in folds_data:
            params = _fit_multinomial(fold_train, **config)
            scores.append(_cross_entropy(fold_valid, params))
        score = sum(scores) / len(scores)
        if score < best_score:
            best_score = score
            best = config
    params = _fit_multinomial(train_rows, **best)
    return params, {
        "cv_enabled": True,
        "folds": len(folds_data),
        "grid_size": len(grid),
        "best_cv_score": round(best_score, 8),
        "best_config": best,
    }


def train_floor_week_m3_timing_model(
    train_rows: list[dict],
    valid_rows: list[dict],
    model_name: str,
    version: str,
    training_mode: str = "standard",
) -> TimingModelArtifact:
    train_eligible = _eligible_rows(train_rows)
    valid_eligible = _eligible_rows(valid_rows)
    if not train_eligible:
        raise ValueError("No leakage-safe complete m3 timing rows available for training")

    if training_mode == "retrain":
        params, tuning_summary = _select_hyperparameters_with_cv(train_eligible, folds=3)
    else:
        params = _fit_multinomial(train_eligible)
        reason = "disabled_to_prevent_fold_leakage" if _has_integrity_metadata(train_eligible) else "not_requested"
        tuning_summary = {"cv_enabled": False, "reason": reason, "folds": 0, "grid_size": 0}

    raw_probs = [predict_week_probabilities(row, params, apply_calibration=False) for row in valid_eligible]
    confidence = [max(probs) for probs in raw_probs]
    outcomes = [
        1 if (max(range(N_CLASSES), key=lambda i: probs[i]) + 1) == int(row["floor_week_m3"]) else 0
        for probs, row in zip(raw_probs, valid_eligible)
    ]
    calibrator = ProbabilityCalibrator(bins=10).fit(confidence, outcomes) if raw_probs else ProbabilityCalibrator(bins=10)
    reliability = calibrator.reliability or {}
    params["calibrator_reliability"] = {str(k): float(v) for k, v in reliability.items()}
    params["tuning_summary"] = tuning_summary
    params["hyperparameter_grid"] = {
        "learning_rate": [0.08, 0.05, 0.03],
        "l2": [0.005, 0.01, 0.03],
        "epochs": [100, 120, 140],
    }

    probabilities = [predict_week_probabilities(row, params) for row in valid_eligible]
    y_true = [int(row["floor_week_m3"]) for row in valid_eligible]
    metrics = timing_metrics(y_true, probabilities)
    metrics["target"] = "floor_week_m3"
    metrics["horizon"] = "m3"
    metrics["train_rows"] = len(train_eligible)
    metrics["validation_rows"] = len(valid_eligible)

    best = [max(range(N_CLASSES), key=lambda i: probs[i]) + 1 for probs in probabilities]
    top3 = [top3_weeks(probs) for probs in probabilities]

    return TimingModelArtifact(
        model_name=model_name,
        horizon="m3",
        target="floor_week_m3",
        version=version,
        params=params,
        metrics=metrics,
        probabilities=probabilities,
        best_class=best,
        top3=top3,
    )