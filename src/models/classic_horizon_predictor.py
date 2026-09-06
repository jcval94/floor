from __future__ import annotations

from typing import Any

from models.robust_range_v3 import (
    ROBUST_RANGE_FEATURES,
    feature_value as robust_feature_value,
    predict_head as predict_robust_head,
    validate_head as validate_robust_head,
)


FEATURES_BY_FAMILY: dict[str, tuple[str, ...]] = {
    "robust_range_v3": ROBUST_RANGE_FEATURES,
    "regularized_linear": (
        "atr_14",
        "trend_context_m3",
        "drawdown_13w",
        "dist_to_low_3m",
        "ai_horizon_alignment",
    ),
    "boosted_stumps": (
        "atr_14",
        "trend_context_m3",
        "drawdown_13w",
        "dist_to_low_3m",
        "ai_horizon_alignment",
        "rel_strength_20",
    ),
    "sequence_linear": (
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
    """Resolve model IDs to the algorithm that is actually executed.

    Legacy IDs remain readable only so old artifacts fail/serve deterministically
    during migration; all newly trained artifacts use truthful names.
    """

    name = str(model_name or "").lower()
    if name.startswith("robust_range_v3_"):
        return "robust_range_v3"
    if name.startswith("regime_median_") or name.startswith("evt_cp_"):
        return "regime_median"
    if name.startswith("boosted_stumps_") or name.startswith("xgboost_"):
        return "boosted_stumps"
    if name.startswith("sequence_linear_") or name.startswith("lstm_"):
        return "sequence_linear"
    if name.startswith("regularized_linear_") or name.startswith("qenet_"):
        return "regularized_linear"
    return ""


def build_runtime_features(row: dict[str, Any]) -> dict[str, float]:
    """Build serving features using the same normalization contract as training."""

    close = _to_float(row.get("close"), 0.0)
    names = sorted({name for values in FEATURES_BY_FAMILY.values() for name in values})
    features: dict[str, float] = {}
    for name in names:
        value = (
            robust_feature_value(row, name, close)
            if name in ROBUST_RANGE_FEATURES
            else _to_float(row.get(name), 0.0)
        )
        features[name] = value
    return features


def validate_family_params(family: str, params: dict[str, Any]) -> None:
    """Validate a serialized trained model contract before serving it."""

    if family == "robust_range_v3":
        validate_robust_head(params)
        return

    if family == "regime_median":
        if not _is_number(params.get("global")):
            raise ValueError("Regime-median params missing numeric global")
        if not isinstance(params.get("table"), dict):
            raise ValueError("Regime-median params missing table mapping")
        cuts = params.get("vol_cuts")
        if not isinstance(cuts, list) or not all(_is_number(value) for value in cuts):
            raise ValueError("Regime-median params vol_cuts must be a numeric list")
        bins_raw = params.get("bins")
        if not _is_number(bins_raw):
            raise ValueError("Regime-median params bins must be a positive integer")
        bins_value = _to_float(bins_raw, 0.0)
        if bins_value <= 0 or bins_value != float(int(bins_value)):
            raise ValueError("Regime-median params bins must be a positive integer")
        return

    if family == "boosted_stumps":
        if not _is_number(params.get("base")) or not _is_number(params.get("lr")):
            raise ValueError("Boosted-stumps params require numeric base and lr")
        stumps = params.get("stumps")
        if not isinstance(stumps, list):
            raise ValueError("Boosted-stumps params stumps must be a list")
        for stump in stumps:
            if not isinstance(stump, dict):
                raise ValueError("Boosted-stumps member must be a mapping")
            if not str(stump.get("feature") or ""):
                raise ValueError("Boosted-stumps member missing feature")
            for key in ("threshold", "left", "right"):
                if not _is_number(stump.get(key)):
                    raise ValueError(f"Boosted-stumps member missing numeric {key}")
        return

    if family in {"regularized_linear", "sequence_linear"}:
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
    *,
    validate: bool = True,
) -> float:
    """Execute one trained classic horizon model exactly from serialized params."""

    if validate:
        validate_family_params(family, params)
    if family == "robust_range_v3":
        return clamp_delta(predict_robust_head(params, features, validate=False))
    if family == "regime_median":
        return _predict_regime_median(params, features)
    if family == "boosted_stumps":
        return _predict_boosted_stumps(params, features)
    if family in {"regularized_linear", "sequence_linear"}:
        return _predict_linear(params, features)
    raise ValueError(f"Unsupported classic horizon family: {family}")


def _predict_regime_median(params: dict[str, Any], features: dict[str, float]) -> float:
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
