from __future__ import annotations

import math
from typing import Iterable

from models.calibration import expected_calibration_error


def pinball_loss(y_true: Iterable[float], y_pred: Iterable[float], alpha: float = 0.2) -> float:
    vals = []
    for t, p in zip(y_true, y_pred):
        e = float(t) - float(p)
        vals.append(max(alpha * e, (alpha - 1) * e))
    return sum(vals) / len(vals) if vals else 0.0


def mae(y_true: Iterable[float], y_pred: Iterable[float]) -> float:
    vals = [abs(float(t) - float(p)) for t, p in zip(y_true, y_pred)]
    return sum(vals) / len(vals) if vals else 0.0


def breach_rate(realized_floor: Iterable[float], predicted_floor: Iterable[float]) -> float:
    vals = [1 if float(r) <= float(p) else 0 for r, p in zip(realized_floor, predicted_floor)]
    return sum(vals) / len(vals) if vals else 0.0


def temporal_stability(series: Iterable[float]) -> float:
    s = [float(x) for x in series]
    if len(s) < 3:
        return 1.0
    diffs = [abs(s[i] - s[i - 1]) for i in range(1, len(s))]
    mean_diff = sum(diffs) / len(diffs)
    scale = (sum(abs(x) for x in s) / len(s)) or 1.0
    return max(0.0, 1.0 - (mean_diff / scale))


def value_metrics(y_true: list[float], y_pred: list[float], confidences: list[float] | None = None) -> dict:
    conf = confidences or [0.5] * len(y_true)
    outcomes = [1 if t <= p else 0 for t, p in zip(y_true, y_pred)]
    return {
        "pinball_loss": pinball_loss(y_true, y_pred, alpha=0.2),
        "mae_realized_floor": mae(y_true, y_pred),
        "breach_rate": breach_rate(y_true, y_pred),
        "calibration_error": expected_calibration_error(conf, outcomes),
        "temporal_stability": temporal_stability(y_pred),
    }


def multiclass_log_loss(y_true: list[int], probs: list[list[float]], eps: float = 1e-12) -> float:
    vals = []
    for yt, pr in zip(y_true, probs):
        p = max(eps, min(1.0, pr[yt - 1]))
        vals.append(-math.log(p))
    return sum(vals) / len(vals) if vals else 0.0


def brier_multiclass(y_true: list[int], probs: list[list[float]], n_classes: int = 13) -> float:
    vals = []
    for yt, pr in zip(y_true, probs):
        one_hot = [1.0 if i + 1 == yt else 0.0 for i in range(n_classes)]
        vals.append(sum((p - y) ** 2 for p, y in zip(pr, one_hot)) / n_classes)
    return sum(vals) / len(vals) if vals else 0.0


def topk_accuracy(y_true: list[int], probs: list[list[float]], k: int) -> float:
    hits = 0
    for yt, pr in zip(y_true, probs):
        top = sorted(range(len(pr)), key=lambda i: pr[i], reverse=True)[:k]
        hits += int((yt - 1) in top)
    return hits / len(y_true) if y_true else 0.0


def expected_week_distance(y_true: list[int], probs: list[list[float]]) -> float:
    vals = []
    for yt, pr in zip(y_true, probs):
        exp = sum((i + 1) * p for i, p in enumerate(pr))
        vals.append(abs(exp - yt))
    return sum(vals) / len(vals) if vals else 0.0


def confusion_matrix(y_true: list[int], y_pred: list[int], n_classes: int = 13) -> dict[int, dict[int, int]]:
    matrix: dict[int, dict[int, int]] = {i: {j: 0 for j in range(1, n_classes + 1)} for i in range(1, n_classes + 1)}
    for t, p in zip(y_true, y_pred):
        matrix[t][p] += 1
    return matrix


def timing_metrics(y_true: list[int], probs: list[list[float]]) -> dict:
    top1 = [max(range(13), key=lambda i: pr[i]) + 1 for pr in probs]
    conf = [max(pr) for pr in probs]
    outcomes = [1 if p == t else 0 for p, t in zip(top1, y_true)]
    return {
        "top1_accuracy": topk_accuracy(y_true, probs, k=1),
        "top3_accuracy": topk_accuracy(y_true, probs, k=3),
        "log_loss": multiclass_log_loss(y_true, probs),
        "brier_score": brier_multiclass(y_true, probs),
        "expected_week_distance": expected_week_distance(y_true, probs),
        "confusion_matrix": confusion_matrix(y_true, top1, n_classes=13),
        "calibration_error": expected_calibration_error(conf, outcomes),
    }


def top3_weeks(probs: list[float]) -> list[dict]:
    top = sorted(range(len(probs)), key=lambda i: probs[i], reverse=True)[:3]
    return [{"week": i + 1, "probability": probs[i]} for i in top]
