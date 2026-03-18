from __future__ import annotations


def _artifact_params(artifact: object | None) -> dict:
    if artifact is None:
        return {}
    if isinstance(artifact, dict):
        params = artifact.get("params", {})
        return params if isinstance(params, dict) else {}

    params = getattr(artifact, "params", None)
    if isinstance(params, dict):
        return params

    params_attr = getattr(artifact, "params_", None)
    if isinstance(params_attr, dict):
        return params_attr

    return {}


def _artifact_meta(artifact: object | None, key: str) -> object | None:
    if artifact is None:
        return None
    if isinstance(artifact, dict):
        return artifact.get(key)
    return getattr(artifact, key, None)


def predict_value_floor_m3(row: dict, artifact: object | None) -> float:
    close = float(row.get("close") or 0.0)
    if not artifact:
        atr = float(row.get("atr_14") or max(0.5, close * 0.01))
        trend = float(row.get("trend_context_m3") or 0.0)
        return close - atr * (8.0 + 2.5 * max(0.0, 1 - trend))

    params = _artifact_params(artifact)
    weights = params.get("weights", {}) if isinstance(params, dict) else {}
    bias = float(params.get("bias", close * 0.95))
    floor_raw = bias + sum(float(row.get(k, 0.0) or 0.0) * float(v) for k, v in weights.items())
    return float(params.get("calibration_scale", 1.0)) * floor_raw


def predict_timing_week_probabilities(row: dict, artifact: object | None) -> list[float]:
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

    reliability = _artifact_params(artifact).get("calibrator_reliability", {})
    if not reliability:
        return probs

    calibrated = []
    for prob in probs:
        idx = min(9, int(max(0.0, min(1.0, prob)) * 10))
        calibrated.append(float(reliability.get(str(idx), reliability.get(idx, prob))))
    total = sum(calibrated)
    return [prob / total for prob in calibrated] if total > 0 else probs


def format_champion_version(value_artifact: object | None, timing_artifact: object | None) -> str:
    """Build a stable and storage-safe champion suite version label.

    Preference order for each artifact:
    1) explicit `version`
    2) version-like suffix derived from `model_name`
    3) `unknown`

    The final format is always: `value:<id>|timing:<id>`.
    """

    def _sanitize_identifier(raw: object) -> str:
        token = "" if raw is None else str(raw).strip()
        if not token:
            return "unknown"

        normalized = []
        for ch in token:
            if ch.isalnum() or ch in {"-", "_", "."}:
                normalized.append(ch)
            else:
                normalized.append("-")

        compact = "".join(normalized).strip("-_.")
        while "--" in compact:
            compact = compact.replace("--", "-")

        return compact or "unknown"

    def _extract_identifier(artifact: object | None) -> str:
        if artifact is None:
            return "unknown"

        version = _artifact_meta(artifact, "version")
        if version not in (None, ""):
            return _sanitize_identifier(version)

        model_name = _artifact_meta(artifact, "model_name")
        if model_name in (None, ""):
            return "unknown"

        model_name_str = str(model_name).strip()

        for separator in ("@", ":"):
            if separator in model_name_str:
                suffix = model_name_str.rsplit(separator, 1)[-1].strip()
                cleaned = _sanitize_identifier(suffix)
                if cleaned != "unknown":
                    return cleaned

        chunks = [chunk for chunk in model_name_str.replace("_", "-").split("-") if chunk]
        for chunk in reversed(chunks):
            if chunk.lower().startswith("v") and any(ch.isdigit() for ch in chunk):
                cleaned = _sanitize_identifier(chunk)
                if cleaned != "unknown":
                    return cleaned

        return "unknown"

    value_version = _extract_identifier(value_artifact)
    timing_version = _extract_identifier(timing_artifact)
    return f"value:{value_version}|timing:{timing_version}"

