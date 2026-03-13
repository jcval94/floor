from __future__ import annotations


def predict_value_floor_m3(row: dict, artifact: dict | None) -> float:
    close = float(row.get("close") or 0.0)
    if not artifact:
        atr = float(row.get("atr_14") or max(0.5, close * 0.01))
        trend = float(row.get("trend_context_m3") or 0.0)
        return close - atr * (8.0 + 2.5 * max(0.0, 1 - trend))

    params = artifact.get("params", {})
    weights = params.get("weights", {})
    bias = float(params.get("bias", close * 0.95))
    floor_raw = bias + sum(float(row.get(k, 0.0) or 0.0) * float(v) for k, v in weights.items())
    return float(params.get("calibration_scale", 1.0)) * floor_raw


def predict_timing_week_probabilities(row: dict, artifact: dict | None) -> list[float]:
    trend = float(row.get("trend_context_m3") or 0.0)
    dd = float(row.get("drawdown_13w") or 0.0)
    align = float(row.get("ai_horizon_alignment") or 0.0)

    center = 7 - int(max(-3, min(3, dd * 10)))
    center = max(1, min(13, center))
    scores = [1.8 - 0.25 * abs(week - center) + 0.35 * align + 0.15 * trend for week in range(1, 14)]
    exps = [pow(2.718281828, score) for score in scores]
    denom = sum(exps) or 1.0
    probs = [value / denom for value in exps]

    if not artifact:
        return probs

    reliability = artifact.get("params", {}).get("calibrator_reliability", {})
    if not reliability:
        return probs

    calibrated = []
    for prob in probs:
        idx = min(9, int(max(0.0, min(1.0, prob)) * 10))
        calibrated.append(float(reliability.get(str(idx), reliability.get(idx, prob))))
    total = sum(calibrated)
    return [prob / total for prob in calibrated] if total > 0 else probs


def format_champion_version(value_artifact: dict | None, timing_artifact: dict | None) -> str:
