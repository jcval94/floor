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


def validate_family_params(family: str, params: dict[str, Any]) -> None:
    """Validate a serialized trained model contract before serving it.

    A malformed trained artifact must never degrade into default deltas. Old
    heuristic artifacts are handled outside this function by the compatibility
    path; once nested trained params exist, they are required to be complete.
    """

    if family == "evt_changepoint_hybrid":
        if not _is_number(params.get("global")):
            raise ValueError("EVT params missing numeric global")
        if not isinstance(params.get("table"), dict):
            raise ValueError("EVT params missing table mapping")
        cuts = params.get("vol_cuts")
        if not isinstance(cuts, list) or not all(_is_number(value) for value in cuts):
            raise ValueError("EVT params vol_cuts must be a numeric list")
        bins = params.get("bins")
        if not _is_number(bins) or int(float(bins)) <= 0:
            raise ValueError("EVT params bins must be a positive integer")
        return

    if family == "xgboost":
        if not _is_number(params.get("base")) or not _is_number(params.get("lr")):
            raise ValueError("XGBoost params require numeric base and lr")
        stumps = params.get("stumps")
        if not isinstance(stumps, list):
            raise ValueError("XGBoost params stumps must be a list")
        for stump in stumps:
            if not isinstance(stump, dict):
                raise ValueError("XGBoost stump must be a mapping")
            if not str(stump.get("feature") or ""):
                raise ValueError("XGBoost stump missing feature")
            for key in ("threshold", "left", "right"):
                if not _is_number(stump.get(key)):
                    raise ValueError(f"XGBoost stump missing numeric {key}")
        return

    if family in {"quantile_elastic_net", "lstm_sequence"}:
        weights = params.get("weights")
        features = params.get("features")
        if not isinstance(weights, dict) or not weights:
            raise ValueError("Linear params require non-empty weights")
        if not isinstance(features, list) or not features:
            raise ValueError("Linear params require non-empty features")
        if not _is_number(params.get("bias")):
            raise ValueError("Linear params require numeric bias")
        missing_weights = [str(name) for name in features if str(name) not in weights]
        if missing_weights:
            raise ValueError(f"Linear params missing weights for features: {missing_weights}")
        if not all(_is_number(value) for value in weights.values()):
            raise ValueError("Linear params weights must be numeric")
        return

    raise ValueError(f"Unsupported classic horizon family: {family}")


def predict_family_delta(
    family: str,
    params: dict[str, Any],
    features: dict[str, float],
) -> float:
    """Execute one trained classic horizon model exactly from its serialized params."""

    validate_family_params(family, params)
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
    pred = _to_float(params.get("bias"), 0.0)
    pred += sum(
        float(weights.get(name, 0.0)) * float(features.get(name, 0.0))
        for name in feature_names
    )
    return clamp_delta(pred)


def _is_number(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


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
