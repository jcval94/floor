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


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _linear_predict(row: dict, weights: dict[str, float], bias: float) -> float:
    return bias + sum(float(row.get(k, 0.0) or 0.0) * w for k, w in weights.items())


def _fit_baseline(train_rows: list[dict], target: str) -> tuple[dict[str, float], float]:
    y = [float(r[target]) for r in train_rows if r.get(target) is not None]
    bias = _mean(y)
    # Stable, leakage-safe hand-crafted weights aligned with current feature stack.
    return ({"atr_14": -0.6, "trend_context_m3": 0.8, "drawdown_13w": 0.4, "dist_to_low_3m": -0.5}, bias)


def train_floor_m3_value_model(train_rows: list[dict], valid_rows: list[dict], model_name: str, version: str) -> ValueModelArtifact:
    target = "floor_m3"
    weights, bias = _fit_baseline(train_rows, target)

    valid_y = [float(r[target]) for r in valid_rows if r.get(target) is not None]
    raw_pred = [_linear_predict(r, weights, bias) for r in valid_rows if r.get(target) is not None]

    calibrator = QuantileCalibrator(alpha=0.2).fit(raw_pred, valid_y)
    pred = calibrator.transform(raw_pred)
    confidences = [0.5 + min(0.45, abs(float(r.get("ai_conviction_long") or 0.0) * 0.4)) for r in valid_rows if r.get(target) is not None]

    metrics = value_metrics(valid_y, pred, confidences)
    metrics["target"] = target
    metrics["horizon"] = "m3"

    return ValueModelArtifact(
        model_name=model_name,
        horizon="m3",
        target=target,
        version=version,
        params={"weights": weights, "bias": bias, "calibration_scale": calibrator.scale},
        metrics=metrics,
        predictions=pred,
        confidences=confidences,
    )
