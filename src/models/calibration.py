from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


@dataclass
class QuantileCalibrator:
    """Simple multiplicative calibrator for quantile-like floor predictions."""

    alpha: float = 0.2
    scale: float = 1.0

    def fit(self, y_pred: Iterable[float], y_true: Iterable[float]) -> "QuantileCalibrator":
        ratios = []
        for p, t in zip(y_pred, y_true):
            if p is None or t is None:
                continue
            if p == 0:
                continue
            ratios.append(float(t) / float(p))
        self.scale = sum(ratios) / len(ratios) if ratios else 1.0
        return self

    def transform(self, y_pred: Iterable[float]) -> list[float]:
        return [float(p) * self.scale for p in y_pred]


@dataclass
class ProbabilityCalibrator:
    """Histogram bin calibrator for multiclass probabilities."""

    bins: int = 10
    reliability: dict[int, float] | None = None

    def fit(self, confidence: Iterable[float], outcomes: Iterable[int]) -> "ProbabilityCalibrator":
        buckets: dict[int, list[int]] = {i: [] for i in range(self.bins)}
        for conf, y in zip(confidence, outcomes):
            idx = min(self.bins - 1, int(_clamp01(float(conf)) * self.bins))
            buckets[idx].append(int(y))
        self.reliability = {
            i: (sum(vals) / len(vals) if vals else (i + 0.5) / self.bins)
            for i, vals in buckets.items()
        }
        return self

    def calibrate(self, probs: list[float]) -> list[float]:
        if not probs:
            return probs
        if self.reliability is None:
            return probs
        out = []
        for p in probs:
            idx = min(self.bins - 1, int(_clamp01(float(p)) * self.bins))
            out.append(_clamp01(self.reliability[idx]))
        s = sum(out)
        if s <= 0:
            return [1.0 / len(probs)] * len(probs)
        return [x / s for x in out]


def expected_calibration_error(confidence: Iterable[float], outcomes: Iterable[int], bins: int = 10) -> float:
    conf = [float(c) for c in confidence]
    outs = [int(o) for o in outcomes]
    if not conf:
        return 0.0

    total = len(conf)
    ece = 0.0
    for b in range(bins):
        lo = b / bins
        hi = (b + 1) / bins
        idx = [i for i, c in enumerate(conf) if (c >= lo and (c < hi or (b == bins - 1 and c <= hi)))]
        if not idx:
            continue
        bin_conf = sum(conf[i] for i in idx) / len(idx)
        bin_acc = sum(outs[i] for i in idx) / len(idx)
        ece += (len(idx) / total) * abs(bin_conf - bin_acc)
    return ece
