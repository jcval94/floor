from __future__ import annotations

from models.train_timing_models import predict_week_probabilities
from models.train_value_models import predict_floor_delta


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
    """Serve m3 value from the exact relative-target training contract."""
    if artifact is None:
        raise ValueError("m3 value champion unavailable")
    params = _artifact_params(artifact)
    if int(params.get("schema_version") or 0) != 2:
        # Compatibility path for the explicitly legacy ChampionModelSet. The
        # canonical ParityChampionModelSet rejects this schema before calling
        # here, so production serving cannot use the old absolute-price model.
        close = float(row.get("close") or 0.0)
        weights = params.get("weights", {}) if isinstance(params, dict) else {}
        bias = float(params.get("bias", close * 0.95))
        floor_raw = bias + sum(
            float(row.get(k, 0.0) or 0.0) * float(v)
            for k, v in weights.items()
        )
        return float(params.get("calibration_scale", 1.0)) * floor_raw
    if params.get("target_space") != "relative_floor_delta":
        raise ValueError("m3 value champion target_space must be relative_floor_delta")

    weights = params.get("weights")
    if not isinstance(weights, dict) or not weights:
        raise ValueError("m3 value champion missing trained weights")
    try:
        bias = float(params["bias"])
        scale = float(params.get("calibration_scale", 1.0))
        close = float(row["close"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("m3 value champion/row missing numeric inference fields") from exc
    if close <= 0:
        raise ValueError("m3 value inference requires positive close")

    delta = predict_floor_delta(
        row,
        {str(key): float(value) for key, value in weights.items()},
        bias,
    )
    delta = max(0.0001, min(0.95, delta * scale))
    return close * (1.0 - delta)


def predict_timing_week_probabilities(row: dict, artifact: object | None) -> list[float]:
    """Serve m3 timing from serialized class-specific coefficients."""
    if artifact is None:
        raise ValueError("m3 timing champion unavailable")
    params = _artifact_params(artifact)
    if int(params.get("schema_version") or 0) != 2:
        # Compatibility only for direct use of the legacy ChampionModelSet.
        trend = float(row.get("trend_context_m3") or 0.0)
        dd = float(row.get("drawdown_13w") or 0.0)
        align = float(row.get("ai_horizon_alignment") or 0.0)
        center = max(1, min(13, 7 - int(max(-3, min(3, dd * 10)))))
        scores = [
            1.8 - 0.25 * abs(week - center) + 0.35 * align + 0.15 * trend
            for week in range(1, 14)
        ]
        exps = [pow(2.718281828, score) for score in scores]
        total = sum(exps) or 1.0
        return [value / total for value in exps]
    return predict_week_probabilities(row, params)


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