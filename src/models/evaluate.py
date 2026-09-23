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
    unique_classes = len(set(top1))
    dominant_share = (
        max(top1.count(label) for label in set(top1)) / len(top1)
        if top1
        else 1.0
    )
    return {
        "top1_accuracy": topk_accuracy(y_true, probs, k=1),
        "top3_accuracy": topk_accuracy(y_true, probs, k=3),
        "log_loss": multiclass_log_loss(y_true, probs),
        "brier_score": brier_multiclass(y_true, probs),
        "expected_week_distance": expected_week_distance(y_true, probs),
        "confusion_matrix": confusion_matrix(y_true, top1, n_classes=13),
        "calibration_error": expected_calibration_error(conf, outcomes),
        "top1_unique_classes": unique_classes,
        "top1_dominant_share": dominant_share,
    }


def _timing_top1_collapse(metrics: dict) -> tuple[int | None, float | None, int]:
    unique_raw = metrics.get("quality_top1_unique_classes")
    dominant_raw = metrics.get("quality_top1_dominant_share")
    if unique_raw is not None and dominant_raw is not None:
        try:
            return int(unique_raw), float(dominant_raw), int(metrics.get("validation_rows", 0) or 0)
        except (TypeError, ValueError):
            pass

    matrix = metrics.get("confusion_matrix")
    if not isinstance(matrix, dict):
        return None, None, 0

    predicted: dict[str, int] = {}
    total = 0
    for row in matrix.values():
        if not isinstance(row, dict):
            continue
        for label, raw_count in row.items():
            try:
                count = int(raw_count)
            except (TypeError, ValueError):
                continue
            if count <= 0:
                continue
            key = str(label)
            predicted[key] = predicted.get(key, 0) + count
            total += count
    if total <= 0:
        return None, None, 0
    unique = sum(1 for count in predicted.values() if count > 0)
    dominant = max(predicted.values()) / total
    return unique, dominant, total


def timing_serving_quality_blocked(metrics: dict) -> bool:
    """Return whether frozen OOS timing evidence is too weak to serve weeks.

    Serving abstains completely when the champion does not beat the uniform
    13-class baseline out of time. Training review must use the same contract
    so an unusable timing champion cannot be classified as merely WARN.
    """

    skill_raw = metrics.get("log_loss_skill")
    quality_raw = metrics.get("quality_log_loss")
    uniform_raw = metrics.get("uniform_log_loss")
    numeric_types = (int, float, str)
    log_loss_blocked = False
    if not (
        isinstance(skill_raw, bool)
        or isinstance(quality_raw, bool)
        or isinstance(uniform_raw, bool)
        or not isinstance(skill_raw, numeric_types)
        or not isinstance(quality_raw, numeric_types)
        or not isinstance(uniform_raw, numeric_types)
    ):
        try:
            skill = float(skill_raw)
            quality_log_loss = float(quality_raw)
            uniform_log_loss = float(uniform_raw)
            log_loss_blocked = skill <= 0.0 or quality_log_loss >= uniform_log_loss
        except (TypeError, ValueError):
            log_loss_blocked = False

    unique_classes, dominant_share, evidence_rows = _timing_top1_collapse(metrics)
    collapse_blocked = (
        evidence_rows >= 30
        and unique_classes is not None
        and dominant_share is not None
        and (unique_classes < 2 or dominant_share >= 0.95)
    )
    return log_loss_blocked or collapse_blocked


def top3_weeks(probs: list[float]) -> list[dict]:
    top = sorted(range(len(probs)), key=lambda i: probs[i], reverse=True)[:3]
    return [{"week": i + 1, "probability": probs[i]} for i in top]
