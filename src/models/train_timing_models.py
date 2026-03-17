from __future__ import annotations

from dataclasses import dataclass
from itertools import product

from models.calibration import ProbabilityCalibrator
from models.evaluate import timing_metrics, top3_weeks


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


def _week_scores(row: dict, config: dict | None = None) -> list[float]:
    trend = float(row.get("trend_context_m3") or 0.0)
    dd = float(row.get("drawdown_13w") or 0.0)
    align = float(row.get("ai_horizon_alignment") or 0.0)
    recency = float(row.get("ai_recency_long") or 5.0)

    cfg = config or {
        "base": 1.8,
        "distance_penalty": 0.25,
        "align_weight": 0.4,
        "recency_weight": -0.03,
        "trend_weight": 0.2,
    }

    # 13-class logits centered by market state. Lower drawdown pushes earlier weeks.
    center = 7 - int(max(-3, min(3, dd * 10)))
    center = max(1, min(13, center))
    scores = []
    for w in range(1, 14):
        dist = abs(w - center)
        score = (
            float(cfg["base"])
            - float(cfg["distance_penalty"]) * dist
            + float(cfg["align_weight"]) * align
            + float(cfg["recency_weight"]) * recency
            + float(cfg["trend_weight"]) * trend
        )
        scores.append(score)
    return scores


def _softmax(scores: list[float]) -> list[float]:
    exps = [pow(2.718281828, s) for s in scores]
    s = sum(exps)
    if s == 0:
        return [1 / len(scores)] * len(scores)
    return [v / s for v in exps]


def _timing_composite_score(metrics: dict) -> float:
    return (
        (1 - float(metrics.get("top1_accuracy", 0.0)))
        + (1 - float(metrics.get("top3_accuracy", 0.0)))
        + float(metrics.get("log_loss", 999.0))
        + float(metrics.get("brier_score", 999.0))
        + float(metrics.get("expected_week_distance", 999.0)) / 13
        + float(metrics.get("calibration_error", 999.0))
    )


def _expanding_time_folds(rows: list[dict], folds: int) -> list[tuple[list[dict], list[dict]]]:
    valid_rows = [r for r in rows if r.get("floor_week_m3") is not None]
    if len(valid_rows) < max(26, folds * 4):
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


def _hyperparameter_grid() -> list[dict]:
    return [
        {
            "base": base,
            "distance_penalty": dist,
            "align_weight": align,
            "recency_weight": recency,
            "trend_weight": trend,
        }
        for base, dist, align, recency, trend in product(
            [1.6, 1.8],
            [0.20, 0.25, 0.30],
            [0.3, 0.4, 0.5],
            [-0.02, -0.03, -0.04],
            [0.1, 0.2, 0.3],
        )
    ]


def _select_hyperparameters_with_cv(train_rows: list[dict], folds: int = 3) -> tuple[dict, dict]:
    folds_data = _expanding_time_folds(train_rows, folds=folds)
    if not folds_data:
        return {
            "base": 1.8,
            "distance_penalty": 0.25,
            "align_weight": 0.4,
            "recency_weight": -0.03,
            "trend_weight": 0.2,
        }, {"cv_enabled": False, "reason": "insufficient_rows", "folds": folds}

    grid = _hyperparameter_grid()
    best_cfg = grid[0]
    best_score = float("inf")

    for cfg in grid:
        fold_scores: list[float] = []
        for _, fold_valid in folds_data:
            valid_target_rows = [r for r in fold_valid if r.get("floor_week_m3") is not None]
            if not valid_target_rows:
                continue
            raw_probs = [_softmax(_week_scores(r, config=cfg)) for r in valid_target_rows]
            confidence = [max(p) for p in raw_probs]
            outcomes = [1 if (max(range(13), key=lambda i: p[i]) + 1) == int(r["floor_week_m3"]) else 0 for p, r in zip(raw_probs, valid_target_rows)]
            calibrator = ProbabilityCalibrator(bins=10).fit(confidence, outcomes)
            calibrated_probs = [calibrator.calibrate(p) for p in raw_probs]
            y_true = [int(r["floor_week_m3"]) for r in valid_target_rows]
            metrics = timing_metrics(y_true, calibrated_probs)
            fold_scores.append(_timing_composite_score(metrics))

        if not fold_scores:
            continue
        score = sum(fold_scores) / len(fold_scores)
        if score < best_score:
            best_score = score
            best_cfg = cfg

    return best_cfg, {
        "cv_enabled": True,
        "folds": len(folds_data),
        "grid_size": len(grid),
        "best_cv_score": round(best_score, 8) if best_score < float("inf") else None,
    }


def train_floor_week_m3_timing_model(
    train_rows: list[dict],
    valid_rows: list[dict],
    model_name: str,
    version: str,
    training_mode: str = "standard",
) -> TimingModelArtifact:
    target = "floor_week_m3"

    score_config = {
        "base": 1.8,
        "distance_penalty": 0.25,
        "align_weight": 0.4,
        "recency_weight": -0.03,
        "trend_weight": 0.2,
    }
    tuning_summary = {"cv_enabled": False, "folds": 0, "grid_size": 0}
    if training_mode == "retrain":
        score_config, tuning_summary = _select_hyperparameters_with_cv(train_rows, folds=3)

    valid_target_rows = [r for r in valid_rows if r.get(target) is not None]
    raw_probs = [_softmax(_week_scores(r, config=score_config)) for r in valid_target_rows]

    confidence = [max(p) for p in raw_probs]
    outcomes = [1 if (max(range(13), key=lambda i: p[i]) + 1) == int(r[target]) else 0 for p, r in zip(raw_probs, valid_target_rows)]
    calibrator = ProbabilityCalibrator(bins=10).fit(confidence, outcomes)

    calibrated_probs = [calibrator.calibrate(p) for p in raw_probs]
    y_true = [int(r[target]) for r in valid_target_rows]
    metrics = timing_metrics(y_true, calibrated_probs)
    metrics["target"] = target
    metrics["horizon"] = "m3"

    best = [max(range(13), key=lambda i: p[i]) + 1 for p in calibrated_probs]
    top3 = [top3_weeks(p) for p in calibrated_probs]

    return TimingModelArtifact(
        model_name=model_name,
        horizon="m3",
        target=target,
        version=version,
        params={
            "calibrator_reliability": calibrator.reliability,
            "score_config": score_config,
            "tuning_summary": tuning_summary,
            "hyperparameter_grid": {
                "base": [1.6, 1.8],
                "distance_penalty": [0.20, 0.25, 0.30],
                "align_weight": [0.3, 0.4, 0.5],
                "recency_weight": [-0.02, -0.03, -0.04],
                "trend_weight": [0.1, 0.2, 0.3],
            },
        },
        metrics=metrics,
        probabilities=calibrated_probs,
        best_class=best,
        top3=top3,
    )
