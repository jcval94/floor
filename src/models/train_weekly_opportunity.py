from __future__ import annotations

from dataclasses import dataclass
import math

from models.temporal_cv import purged_expanding_folds


FEATURE_NAMES = (
    "momentum_20",
    "rel_strength_20",
    "trend_context_m3",
    "drawdown_13w",
    "atr_ratio_14",
    "price_position_in_range_20",
)
TARGET_CLIP = 3.0


@dataclass
class WeeklyOpportunityArtifact:
    model_name: str
    horizon: str
    target: str
    version: str
    params: dict
    metrics: dict
    predictions: list[float]


def _to_float(value: object, default: float = 0.0) -> float:
    if not isinstance(value, (int, float, str, bytes, bytearray)):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _target(row: dict) -> float | None:
    if row.get("split_eligible_q1", True) is False:
        return None
    forward = row.get("forward_return_q1")
    close = _to_float(row.get("close"))
    floor = row.get("floor_q1")
    if forward in (None, "") or floor in (None, "") or close <= 0:
        return None
    downside = max(0.01, (close - _to_float(floor)) / close)
    ratio = _to_float(forward) / downside
    return max(-TARGET_CLIP, min(TARGET_CLIP, ratio))


def _raw_features(row: dict) -> list[float]:
    close = max(_to_float(row.get("close")), 1e-9)
    return [
        _to_float(row.get("momentum_20")),
        _to_float(row.get("rel_strength_20")),
        _to_float(row.get("trend_context_m3")),
        _to_float(row.get("drawdown_13w")),
        abs(_to_float(row.get("atr_14"))) / close,
        _to_float(row.get("price_position_in_range_20"), 0.5),
    ]


def _usable(rows: list[dict]) -> list[dict]:
    return [row for row in rows if _target(row) is not None]


def _fit_scaler(rows: list[dict]) -> tuple[list[float], list[float]]:
    vectors = [_raw_features(row) for row in rows]
    means = [sum(vector[j] for vector in vectors) / len(vectors) for j in range(len(FEATURE_NAMES))]
    scales: list[float] = []
    for j, mean in enumerate(means):
        variance = sum((vector[j] - mean) ** 2 for vector in vectors) / max(1, len(vectors))
        scales.append(max(math.sqrt(variance), 1e-6))
    return means, scales


def _scaled(row: dict, means: list[float], scales: list[float]) -> list[float]:
    values = _raw_features(row)
    return [(values[j] - means[j]) / scales[j] for j in range(len(values))]


def _fit_ridge(rows: list[dict], *, l2: float = 0.03, lr: float = 0.02, epochs: int = 300) -> dict:
    usable = _usable(rows)
    if not usable:
        raise ValueError("No complete q1 opportunity rows available")
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
        "schema_version": 1,
        "model_type": "ridge_risk_adjusted_ranker",
        "feature_names": list(FEATURE_NAMES),
        "feature_means": means,
        "feature_scales": scales,
        "weights": weights,
        "bias": bias,
        "l2": l2,
        "learning_rate": lr,
        "epochs": epochs,
        "target_clip": TARGET_CLIP,
        "train_rows": len(usable),
    }


def predict_weekly_opportunity(row: dict, params: dict) -> float:
    if params.get("model_type") != "ridge_risk_adjusted_ranker":
        raise ValueError("Unsupported weekly opportunity model")
    names = params.get("feature_names")
    if names != list(FEATURE_NAMES):
        raise ValueError("Weekly opportunity feature contract mismatch")
    means = [float(value) for value in params.get("feature_means", [])]
    scales = [max(abs(float(value)), 1e-6) for value in params.get("feature_scales", [])]
    weights = [float(value) for value in params.get("weights", [])]
    if not (len(means) == len(scales) == len(weights) == len(FEATURE_NAMES)):
        raise ValueError("Weekly opportunity parameter shape mismatch")
    x = _scaled(row, means, scales)
    raw = float(params.get("bias") or 0.0) + sum(weight * value for weight, value in zip(weights, x))
    return max(-TARGET_CLIP, min(TARGET_CLIP, raw))


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _rank(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda idx: values[idx])
    ranks = [0.0] * len(values)
    for rank, idx in enumerate(order, start=1):
        ranks[idx] = float(rank)
    return ranks


def _correlation(a: list[float], b: list[float]) -> float:
    if len(a) < 2 or len(a) != len(b):
        return 0.0
    mean_a = _mean(a)
    mean_b = _mean(b)
    numerator = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
    denom_a = math.sqrt(sum((x - mean_a) ** 2 for x in a))
    denom_b = math.sqrt(sum((y - mean_b) ** 2 for y in b))
    denom = denom_a * denom_b
    return numerator / denom if denom > 0 else 0.0


def _metrics(rows: list[dict], predictions: list[float]) -> dict:
    usable = _usable(rows)
    true = [float(_target(row) or 0.0) for row in usable]
    forwards = [_to_float(row.get("forward_return_q1")) for row in usable]
    mae = _mean([abs(t - p) for t, p in zip(true, predictions)])
    directional = _mean([1.0 if (t >= 0) == (p >= 0) else 0.0 for t, p in zip(true, predictions)])
    rank_corr = _correlation(_rank(true), _rank(predictions)) if true else 0.0

    count = len(predictions)
    top_n = max(1, math.ceil(count * 0.20)) if count else 0
    top_idx = sorted(range(count), key=lambda idx: predictions[idx], reverse=True)[:top_n]
    top_returns = [forwards[idx] for idx in top_idx]
    all_mean = _mean(forwards)
    top_mean = _mean(top_returns)
    return {
        "mae_opportunity_score": mae,
        "directional_accuracy": directional,
        "spearman_rank_correlation": rank_corr,
        "mean_forward_return_q1": all_mean,
        "top_quintile_mean_forward_return_q1": top_mean,
        "top_quintile_return_lift": top_mean - all_mean,
        "top_quintile_positive_rate": _mean([1.0 if value > 0 else 0.0 for value in top_returns]),
        "validation_rows": count,
    }


def _cv_score(rows: list[dict], params: dict) -> float:
    usable = _usable(rows)
    if not usable:
        return float("inf")
    predictions = [predict_weekly_opportunity(row, params) for row in usable]
    return float(_metrics(usable, predictions)["mae_opportunity_score"])


def _select_hyperparameters(train_rows: list[dict]) -> tuple[float, float, dict]:
    folds = purged_expanding_folds(
        _usable(train_rows),
        target_end_field="target_end_date_q1",
        folds=3,
        min_train_dates=20,
    )
    if not folds:
        return 0.03, 0.02, {"cv_enabled": False, "reason": "insufficient_purged_temporal_folds", "folds": 0}
    grid = ((0.01, 0.02), (0.03, 0.02), (0.08, 0.01))
    best = grid[0]
    best_score = float("inf")
    for l2, lr in grid:
        scores: list[float] = []
        for fold_train, fold_valid in folds:
            params = _fit_ridge(fold_train, l2=l2, lr=lr, epochs=240)
            scores.append(_cv_score(fold_valid, params))
        score = _mean(scores)
        if score < best_score:
            best_score = score
            best = (l2, lr)
    return best[0], best[1], {
        "cv_enabled": True,
        "folds": len(folds),
        "grid_size": len(grid),
        "best_cv_mae": best_score,
    }


def train_weekly_opportunity_model(
    train_rows: list[dict],
    valid_rows: list[dict],
    *,
    model_name: str = "weekly_opportunity_ridge",
    version: str = "v1",
    tune: bool = True,
) -> WeeklyOpportunityArtifact:
    train = _usable(train_rows)
    valid = _usable(valid_rows)
    if not train or not valid:
        raise ValueError("Weekly opportunity model requires complete q1 train and validation rows")

    l2, lr, tuning = _select_hyperparameters(train) if tune else (0.03, 0.02, {"cv_enabled": False, "reason": "not_requested", "folds": 0})
    params = _fit_ridge(train, l2=l2, lr=lr, epochs=320)
    params["tuning_summary"] = tuning
    params["purpose"] = "rank 10-session opportunities for low-frequency portfolio review"
    params["canonical_serving_enabled"] = False

    predictions = [predict_weekly_opportunity(row, params) for row in valid]
    metrics = _metrics(valid, predictions)
    metrics.update(
        {
            "horizon": "q1_10_sessions",
            "target": "forward_return_q1 / max(realized_drawdown_q1, 1%)",
            "train_rows": len(train),
        }
    )
    return WeeklyOpportunityArtifact(
        model_name=model_name,
        horizon="q1",
        target="risk_adjusted_opportunity_q1",
        version=version,
        params=params,
        metrics=metrics,
        predictions=predictions,
    )
