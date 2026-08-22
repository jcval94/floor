from __future__ import annotations

from typing import Any


FEATURES_BY_FAMILY: dict[str, tuple[str, ...]] = {
    "quantile_elastic_net": (
        "atr_14",
        "trend_context_m3",
        "drawdown_13w",
        "dist_to_low_3m",
        "ai_horizon_alignment",
    ),
    "xgboost": (
        "atr_14",
        "trend_context_m3",
        "drawdown_13w",
        "dist_to_low_3m",
        "ai_horizon_alignment",
        "rel_strength_20",
    ),
    "lstm_sequence": (
        "momentum_20",
        "trend_context_m3",
        "ai_horizon_alignment",
        "ai_recency_long",
        "atr_14",
    ),
}


def clamp_delta(value: float) -> float:
    return max(0.0001, min(0.7, float(value)))


def model_family(model_name: str) -> str:
    name = str(model_name or "").lower()
    if name.startswith("evt_cp_"):
        return "evt_changepoint_hybrid"
    if name.startswith("xgboost_"):
        return "xgboost"
    if name.startswith("lstm_"):
        return "lstm_sequence"
    if name.startswith("qenet_"):
        return "quantile_elastic_net"
    return ""


def build_runtime_features(row: dict[str, Any]) -> dict[str, float]:
    """Build serving features using the same normalization contract as training."""

    close = _to_float(row.get("close"), 0.0)
    names = sorted({name for values in FEATURES_BY_FAMILY.values() for name in values})
    features: dict[str, float] = {}
    for name in names:
        value = _to_float(row.get(name), 0.0)
        if name == "atr_14":
            value = value / max(close, 1.0)
        features[name] = value
    return features


def predict_family_delta(
    family: str,
    params: dict[str, Any],
    features: dict[str, float],
) -> float:
    """Execute one trained classic horizon model exactly from its serialized params."""

    if family == "evt_changepoint_hybrid":
        return _predict_evt(params, features)
    if family == "xgboost":
        return _predict_boosted_stumps(params, features)
    if family in {"quantile_elastic_net", "lstm_sequence"}:
        return _predict_linear(params, features)
    raise ValueError(f"Unsupported classic horizon family: {family}")


def _predict_evt(params: dict[str, Any], features: dict[str, float]) -> float:
    bins = int(_to_float(params.get("bins"), 3.0)) or 3
    cuts = [_to_float(value, 0.0) for value in _as_list(params.get("vol_cuts"))]
    trend_bucket = "up" if float(features.get("trend_context_m3", 0.0)) >= 0 else "down"
    vol = abs(float(features.get("atr_14", 0.0)))
    vol_bucket = bins
    for idx, cut in enumerate(cuts, start=1):
        if vol <= cut:
            vol_bucket = idx
            break
    table = _float_dict(params.get("table"))
    key = f"v{vol_bucket}:{trend_bucket}"
    return clamp_delta(_to_float(table.get(key, params.get("global", 0.01)), 0.01))


def _predict_boosted_stumps(params: dict[str, Any], features: dict[str, float]) -> float:
    pred = _to_float(params.get("base"), 0.01)
    lr = _to_float(params.get("lr"), 0.45)
    for stump in _as_list(params.get("stumps")):
        if not isinstance(stump, dict):
            continue
        feature = str(stump.get("feature") or "")
        threshold = _to_float(stump.get("threshold"), 0.0)
        left = _to_float(stump.get("left"), 0.0)
        right = _to_float(stump.get("right"), 0.0)
        pred += lr * (left if float(features.get(feature, 0.0)) <= threshold else right)
    return clamp_delta(pred)


def _predict_linear(params: dict[str, Any], features: dict[str, float]) -> float:
    weights = _float_dict(params.get("weights"))
    feature_names = [str(value) for value in _as_list(params.get("features"))]
    if not feature_names:
        feature_names = sorted(weights)
    pred = _to_float(params.get("bias"), 0.0)
    pred += sum(
        float(weights.get(name, 0.0)) * float(features.get(name, 0.0))
        for name in feature_names
    )
    return clamp_delta(pred)


def _to_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _float_dict(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    return {str(key): _to_float(raw, 0.0) for key, raw in value.items()}
