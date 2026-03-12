from __future__ import annotations

from dataclasses import dataclass

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


def _week_scores(row: dict) -> list[float]:
    trend = float(row.get("trend_context_m3") or 0.0)
    dd = float(row.get("drawdown_13w") or 0.0)
    align = float(row.get("ai_horizon_alignment") or 0.0)
    recency = float(row.get("ai_recency_long") or 5.0)

    # 13-class logits centered by market state. Lower drawdown pushes earlier weeks.
    center = 7 - int(max(-3, min(3, dd * 10)))
    center = max(1, min(13, center))
    scores = []
    for w in range(1, 14):
        dist = abs(w - center)
        score = 1.8 - 0.25 * dist + 0.4 * align - 0.03 * recency + 0.2 * trend
        scores.append(score)
    return scores


def _softmax(scores: list[float]) -> list[float]:
    exps = [pow(2.718281828, s) for s in scores]
    s = sum(exps)
    if s == 0:
        return [1 / len(scores)] * len(scores)
    return [v / s for v in exps]


def train_floor_week_m3_timing_model(train_rows: list[dict], valid_rows: list[dict], model_name: str, version: str) -> TimingModelArtifact:
    _ = train_rows  # pattern-compatible placeholder for future fit logic.
    target = "floor_week_m3"

    valid_target_rows = [r for r in valid_rows if r.get(target) is not None]
    raw_probs = [_softmax(_week_scores(r)) for r in valid_target_rows]

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
        params={"calibrator_reliability": calibrator.reliability},
        metrics=metrics,
        probabilities=calibrated_probs,
        best_class=best,
        top3=top3,
    )
