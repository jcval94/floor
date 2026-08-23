from __future__ import annotations

from dataclasses import dataclass
import math
from typing import TypedDict

from models.evaluate import timing_metrics, top3_weeks
from models.temporal_cv import chronological_calibration_split, purged_expanding_folds


FEATURE_NAMES = (
    "atr_ratio_14",
    "trend_context_m3",
    "drawdown_13w",
    "dist_to_low_3m",
    "momentum_20",
)
N_CLASSES = 13
UNIFORM_PROBABILITY = 1.0 / N_CLASSES
ABSTAIN_CONFIDENCE = 0.12


class TrainingConfig(TypedDict):
    learning_rate: float
    l2: float
    epochs: int


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
    if not isinstance(value, (int, float, str, bytes, bytearray)):
        return default
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
    means = [sum(vector[j] for vector in vectors) / len(vectors) for j in range(len(FEATURE_NAMES))]
    scales: list[float] = []
    for j, mean in enumerate(means):
        variance = sum((vector[j] - mean) ** 2 for vector in vectors) / max(1, len(vectors))
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


def _ordinal_target(target: int) -> list[float]:
    """Distance-aware target for an ordered 13-week outcome."""
    values = [0.0] * N_CLASSES
    center = target - 1
    values[center] = 0.60
    if center > 0:
        values[center - 1] = 0.20
    if center + 1 < N_CLASSES:
        values[center + 1] = 0.20
    total = sum(values) or 1.0
    return [value / total for value in values]


def _fit_multinomial(
    rows: list[dict],
    *,
    learning_rate: float = 0.05,
    l2: float = 0.01,
    epochs: int = 140,
) -> dict:
    eligible = _eligible_rows(rows)
    if not eligible:
        raise ValueError("No eligible m3 timing rows")

    means, scales = _fit_scaler(eligible)
    x = [_scaled_vector(row, means, scales) for row in eligible]
    y = [int(row["floor_week_m3"]) for row in eligible]
    n = float(len(eligible))
    feature_count = len(FEATURE_NAMES)

    counts = [1.0] * N_CLASSES
    for target in y:
        counts[target - 1] += 1.0
    count_total = sum(counts)
    bias = [math.log(count / count_total) for count in counts]
    weights = [[0.0] * feature_count for _ in range(N_CLASSES)]

    for _ in range(epochs):
        grad_w = [[0.0] * feature_count for _ in range(N_CLASSES)]
        grad_b = [0.0] * N_CLASSES
        for vector, target in zip(x, y):
            logits = [
                bias[k] + sum(weights[k][j] * vector[j] for j in range(feature_count))
                for k in range(N_CLASSES)
            ]
            probs = _softmax(logits)
            soft_target = _ordinal_target(target)
            for k in range(N_CLASSES):
                error = probs[k] - soft_target[k]
                grad_b[k] += error / n
                for j in range(feature_count):
                    grad_w[k][j] += error * vector[j] / n
        for k in range(N_CLASSES):
            bias[k] -= learning_rate * grad_b[k]
            for j in range(feature_count):
                gradient = grad_w[k][j] + l2 * weights[k][j]
                weights[k][j] -= learning_rate * gradient

    return {
        "schema_version": 2,
        "model_type": "multinomial_logistic",
        "objective": "ordinal_neighbor_smoothed_cross_entropy",
        "class_count": N_CLASSES,
        "feature_names": list(FEATURE_NAMES),
        "feature_means": {name: means[i] for i, name in enumerate(FEATURE_NAMES)},
        "feature_scales": {name: scales[i] for i, name in enumerate(FEATURE_NAMES)},
        "weights": weights,
        "bias": bias,
        "learning_rate": learning_rate,
        "l2": l2,
        "epochs": epochs,
        "temperature": 1.0,
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


def predict_week_probabilities(
    row: dict,
    params: dict,
    *,
    apply_calibration: bool = True,
) -> list[float]:
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
    temperature = max(0.25, min(4.0, _to_float(params.get("temperature"), 1.0))) if apply_calibration else 1.0
    probs = _softmax([value / temperature for value in logits])
    total = sum(probs)
    if total <= 0:
        return [UNIFORM_PROBABILITY] * N_CLASSES
    return [max(0.0, value) / total for value in probs]


def _cross_entropy(rows: list[dict], params: dict, *, calibrated: bool = False) -> float:
    eligible = _eligible_rows(rows)
    if not eligible:
        return float("inf")
    losses = []
    for row in eligible:
        probs = predict_week_probabilities(row, params, apply_calibration=calibrated)
        target = int(row["floor_week_m3"]) - 1
        losses.append(-math.log(max(1e-12, probs[target])))
    return sum(losses) / len(losses)


def _has_integrity_metadata(rows: list[dict]) -> bool:
    return any("target_end_date_m3" in row for row in rows)


def _expanding_time_folds(rows: list[dict], folds: int) -> list[tuple[list[dict], list[dict]]]:
    eligible = _eligible_rows(rows)
    if _has_integrity_metadata(eligible):
        return purged_expanding_folds(
            eligible,
            target_end_field="target_end_date_m3",
            folds=folds,
            min_train_dates=20,
        )
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


def _fit_from_config(rows: list[dict], config: TrainingConfig) -> dict:
    return _fit_multinomial(
        rows,
        learning_rate=config["learning_rate"],
        l2=config["l2"],
        epochs=config["epochs"],
    )


def _select_hyperparameters_with_cv(train_rows: list[dict], folds: int = 3) -> tuple[dict, dict]:
    folds_data = _expanding_time_folds(train_rows, folds=folds)
    default: TrainingConfig = {"learning_rate": 0.05, "l2": 0.01, "epochs": 140}
    if not folds_data:
        params = _fit_from_config(train_rows, default)
        return params, {
            "cv_enabled": False,
            "reason": "insufficient_purged_temporal_folds",
            "folds": 0,
            "grid_size": 0,
        }

    grid: list[TrainingConfig] = [
        {"learning_rate": 0.08, "l2": 0.005, "epochs": 120},
        {"learning_rate": 0.05, "l2": 0.01, "epochs": 140},
        {"learning_rate": 0.03, "l2": 0.03, "epochs": 180},
    ]
    best = grid[0]
    best_score = float("inf")
    for config in grid:
        scores = []
        for fold_train, fold_valid in folds_data:
            params = _fit_from_config(fold_train, config)
            scores.append(_cross_entropy(fold_valid, params))
        score = sum(scores) / len(scores)
        if score < best_score:
            best_score = score
            best = config
    params = _fit_from_config(train_rows, best)
    return params, {
        "cv_enabled": True,
        "folds": len(folds_data),
        "grid_size": len(grid),
        "best_cv_log_loss": round(best_score, 8),
        "best_config": best,
    }


def _fit_temperature(rows: list[dict], params: dict) -> float:
    eligible = _eligible_rows(rows)
    if not eligible:
        return 1.0
    candidates = (0.70, 0.85, 1.0, 1.20, 1.50, 2.0)
    best_temperature = 1.0
    best_loss = float("inf")
    for temperature in candidates:
        trial = dict(params)
        trial["temperature"] = temperature
        loss = _cross_entropy(eligible, trial, calibrated=True)
        if loss < best_loss:
            best_loss = loss
            best_temperature = temperature
    return best_temperature


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
    if not valid_eligible:
        raise ValueError("No leakage-safe complete m3 timing rows available for validation")

    if training_mode == "retrain":
        params, tuning_summary = _select_hyperparameters_with_cv(train_eligible, folds=3)
    else:
        params = _fit_multinomial(train_eligible)
        tuning_summary = {"cv_enabled": False, "reason": "not_requested", "folds": 0, "grid_size": 0}

    calibration_rows, evaluation_rows = chronological_calibration_split(valid_eligible)
    params["temperature"] = _fit_temperature(calibration_rows, params)
    params["calibration_method"] = "chronological_holdout_temperature"
    params["calibrator_reliability"] = {}
    params["tuning_summary"] = tuning_summary
    params["hyperparameter_grid"] = {
        "learning_rate": [0.08, 0.05, 0.03],
        "l2": [0.005, 0.01, 0.03],
        "epochs": [120, 140, 180],
    }

    evaluation_rows = _eligible_rows(evaluation_rows)
    if not evaluation_rows:
        evaluation_rows = valid_eligible

    # Quality/selection metrics come only from the later out-of-time evaluation
    # half. Monitoring compatibility keys are computed over the complete
    # explicit validation split so a review of the same dataset does not look
    # like performance decay merely because calibration was introduced.
    probabilities = [predict_week_probabilities(row, params) for row in evaluation_rows]
    y_true = [int(row["floor_week_m3"]) for row in evaluation_rows]
    quality_metrics = timing_metrics(y_true, probabilities)

    monitoring_probabilities = [predict_week_probabilities(row, params) for row in valid_eligible]
    monitoring_y_true = [int(row["floor_week_m3"]) for row in valid_eligible]
    metrics = timing_metrics(monitoring_y_true, monitoring_probabilities)

    uniform_log_loss = math.log(N_CLASSES)
    confidences = [max(probs) for probs in probabilities]
    metrics.update(
        {
            "quality_top1_accuracy": float(quality_metrics.get("top1_accuracy", 0.0)),
            "quality_top3_accuracy": float(quality_metrics.get("top3_accuracy", 0.0)),
            "quality_log_loss": float(quality_metrics.get("log_loss", uniform_log_loss)),
            "quality_brier_score": float(quality_metrics.get("brier_score", 0.0)),
            "quality_expected_week_distance": float(quality_metrics.get("expected_week_distance", 0.0)),
            "quality_calibration_error": float(quality_metrics.get("calibration_error", 0.0)),
            "uniform_log_loss": uniform_log_loss,
            "log_loss_skill": 1.0 - (float(quality_metrics.get("log_loss", uniform_log_loss)) / uniform_log_loss),
            "mean_max_probability": sum(confidences) / len(confidences) if confidences else 0.0,
            "abstention_threshold": ABSTAIN_CONFIDENCE,
            "abstention_rate": (
                sum(1 for confidence in confidences if confidence < ABSTAIN_CONFIDENCE) / len(confidences)
                if confidences
                else 1.0
            ),
            "target": "floor_week_m3",
            "horizon": "m3",
            "train_rows": len(train_eligible),
            "calibration_rows": len(calibration_rows),
            "validation_rows": len(evaluation_rows),
            "monitoring_validation_rows": len(valid_eligible),
        }
    )

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
