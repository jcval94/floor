from __future__ import annotations

import math
from typing import Any, Iterable

from models.robust_range_v3 import (
    build_anchored_blend,
    export_hist_gradient_boosting,
    fit_ceiling_head,
    predict_head as predict_robust_head,
    validate_head as validate_robust_head,
)


# Frozen from experiments/central_skill_d1_w1/strict_validation.
# These values are intentionally not tuned by the production trainer.
STRICT_VALIDATION_EVIDENCE = "central_skill_d1_w1_strict_20260925"
ENSEMBLE_WEIGHTS = {
    "spread_asymmetry": 1.0 / 3.0,
    "residual_boosted": 1.0 / 3.0,
    "atr_model_shrinkage": 1.0 / 3.0,
}
SHRINKAGE_LAMBDA = {"d1": 0.40, "w1": 0.40}
RIDGE_ALPHA = 1.0
CENTRAL_CLIP_MIN = 0.0001
CENTRAL_CLIP_MAX = 0.60

CENTRAL_SKILL_FEATURES: tuple[str, ...] = (
    "atr_14",
    "rolling_vol_5",
    "rolling_vol_20",
    "rolling_vol_60",
    "downside_vol_20",
    "parkinson_vol_20",
    "gap_open_to_prev_close",
    "relative_volume_20",
    "dist_to_low_20",
    "dist_to_high_20",
    "sma_slope_5_20",
    "beta_20",
    "rel_strength_20",
    "momentum_10",
    "momentum_20",
    "vol_regime_score",
    "recent_drawdown_20",
    "range_width_5",
    "range_width_20",
    "range_width_60",
    "price_position_in_range_20",
    "trend_context_m3",
    "slope_4w",
    "slope_8w",
    "slope_13w",
    "drawdown_13w",
    "range_compression_20_60",
    "rel_strength_4w",
    "rel_strength_8w",
    "rel_strength_13w",
    "dist_to_low_3m",
    "dist_to_low_6m",
    "dist_to_low_12m",
    "vol_persistence_20_60",
    "range_amp_daily_5",
    "range_amp_daily_13",
    "rsi_14",
    "bollinger_width_20",
    "vwap_distance",
    "month_sin",
    "month_cos",
)

CURRENT_ANCHOR_FEATURES: tuple[str, ...] = (
    "atr_14",
    "trend_context_m3",
    "drawdown_13w",
    "dist_to_low_3m",
    "ai_horizon_alignment",
    "rel_strength_20",
)

RESIDUAL_HGB_PARAMS: dict[str, float | int | bool | str] = {
    "loss": "absolute_error",
    "learning_rate": 0.05,
    "max_iter": 60,
    "max_leaf_nodes": 7,
    "min_samples_leaf": 80,
    "l2_regularization": 0.1,
    "early_stopping": False,
    "random_state": 260925,
}


def _number(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool) or value is None:
        return default
    if not isinstance(value, (str, int, float)):
        return default
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _finite_or_nan(value: object) -> float:
    if isinstance(value, bool) or value is None:
        return float("nan")
    if not isinstance(value, (str, int, float)):
        return float("nan")
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return out if math.isfinite(out) else float("nan")


def _clip(value: float) -> float:
    return max(CENTRAL_CLIP_MIN, min(CENTRAL_CLIP_MAX, float(value)))


def runtime_feature_value(
    row: dict[str, Any],
    name: str,
    close: float,
    normalized_features: dict[str, float],
) -> float:
    """Preserve missingness for the frozen Ridge components only."""

    if name in {"atr_14", "month_sin", "month_cos"}:
        return float(normalized_features.get(name, float("nan")))
    raw = row.get(name)
    if raw is None:
        return float("nan")
    value = _finite_or_nan(raw)
    if not math.isfinite(value):
        return float("nan")
    return float(normalized_features.get(name, value))


def training_feature_payload(item: Any) -> dict[str, float]:
    """Build the exact feature payload used by serialized serving."""

    features = dict(item.features)
    for name in CENTRAL_SKILL_FEATURES:
        features[f"central__{name}"] = _training_feature(
            item,
            name,
            preserve_missing=True,
        )
    return features


def _training_feature(item: Any, name: str, *, preserve_missing: bool) -> float:
    if name in {"atr_14", "month_sin", "month_cos"}:
        return float(item.features.get(name, 0.0))
    if preserve_missing and item.row.get(name) is None:
        return float("nan")
    value = item.features.get(name)
    if value is None:
        value = item.row.get(name)
    return _finite_or_nan(value) if preserve_missing else _number(value, 0.0)


def _matrix(
    rows: Iterable[Any],
    names: tuple[str, ...],
    *,
    preserve_missing: bool,
) -> list[list[float]]:
    return [
        [
            _training_feature(item, name, preserve_missing=preserve_missing)
            for name in names
        ]
        for item in rows
    ]


def _fit_ridge(rows: list[Any], target: list[float]) -> dict[str, Any]:
    try:
        import numpy as np
        from sklearn.impute import SimpleImputer
        from sklearn.linear_model import Ridge
        from sklearn.preprocessing import StandardScaler
    except ImportError as exc:  # pragma: no cover - Actions installs modeling deps
        raise RuntimeError(
            "central_skill_ensemble_v1 training requires modeling dependencies"
        ) from exc

    x = np.asarray(
        _matrix(rows, CENTRAL_SKILL_FEATURES, preserve_missing=True),
        dtype=np.float64,
    )
    # SimpleImputer drops columns that are entirely missing. Preserve the
    # serialized feature contract by defining a neutral zero for such columns;
    # ordinary partially-missing columns still use the experimental median rule.
    all_missing = np.isnan(x).all(axis=0)
    if bool(np.any(all_missing)):
        x[:, all_missing] = 0.0
    y = np.asarray(target, dtype=np.float64)
    imputer = SimpleImputer(strategy="median")
    x_imp = imputer.fit_transform(x)
    scaler = StandardScaler()
    x_scaled = scaler.fit_transform(x_imp)
    model = Ridge(alpha=RIDGE_ALPHA)
    model.fit(x_scaled, y)
    return {
        "kind": "standardized_ridge",
        "features": list(CENTRAL_SKILL_FEATURES),
        "impute": [float(v) for v in imputer.statistics_],
        "mean": [float(v) for v in scaler.mean_],
        "scale": [float(v) if float(v) != 0.0 else 1.0 for v in scaler.scale_],
        "coef": [float(v) for v in model.coef_],
        "intercept": float(model.intercept_),
        "alpha": RIDGE_ALPHA,
    }


def _predict_ridge(params: dict[str, Any], features: dict[str, float]) -> float:
    names = [str(value) for value in params["features"]]
    impute = [float(value) for value in params["impute"]]
    mean = [float(value) for value in params["mean"]]
    scale = [float(value) for value in params["scale"]]
    coef = [float(value) for value in params["coef"]]
    pred = float(params["intercept"])
    for idx, name in enumerate(names):
        raw = features.get(f"central__{name}", features.get(name, float("nan")))
        value = float(raw) if isinstance(raw, (int, float)) else float("nan")
        if not math.isfinite(value):
            value = impute[idx]
        denom = scale[idx] if scale[idx] != 0.0 else 1.0
        pred += coef[idx] * ((value - mean[idx]) / denom)
    return pred


def _validate_ridge(params: dict[str, Any]) -> None:
    if params.get("kind") != "standardized_ridge":
        raise ValueError("Expected standardized_ridge params")
    names = params.get("features")
    if not isinstance(names, list) or not names:
        raise ValueError("Ridge params require features")
    expected = len(names)
    for key in ("impute", "mean", "scale", "coef"):
        values = params.get(key)
        if not isinstance(values, list) or len(values) != expected:
            raise ValueError(f"Ridge params {key} length mismatch")
        if not all(isinstance(value, (int, float)) for value in values):
            raise ValueError(f"Ridge params {key} must be numeric")
    if not isinstance(params.get("intercept"), (int, float)):
        raise ValueError("Ridge params require numeric intercept")


def _atr_multiplier(rows: list[Any], side: str) -> float:
    values: list[float] = []
    for item in rows:
        atr = abs(float(item.features.get("atr_14", 0.0)))
        if atr <= 1e-8:
            continue
        target = (
            float(item.floor_delta)
            if side == "floor"
            else float(item.ceiling_delta)
        )
        values.append(target / atr)
    if not values:
        raise ValueError("ATR anchor requires non-zero ATR training rows")
    values.sort()
    midpoint = len(values) // 2
    if len(values) % 2:
        return values[midpoint]
    return (values[midpoint - 1] + values[midpoint]) / 2.0


def _fit_anchor_stumps(
    rows: list[Any],
    side: str,
    *,
    rounds: int = 6,
    learning_rate: float = 0.45,
) -> dict[str, Any]:
    targets = [
        float(item.floor_delta if side == "floor" else item.ceiling_delta)
        for item in rows
    ]
    base = sum(targets) / len(targets)
    predictions = [base for _ in rows]
    stumps: list[dict[str, float | str]] = []

    for _ in range(rounds):
        residuals = [
            target - prediction
            for target, prediction in zip(targets, predictions)
        ]
        best: dict[str, float | str] | None = None
        best_error = float("inf")
        for name in CURRENT_ANCHOR_FEATURES:
            values = [
                _training_feature(item, name, preserve_missing=False)
                for item in rows
            ]
            ordered = sorted(values)
            threshold = ordered[len(ordered) // 2]
            left = [
                residual
                for residual, value in zip(residuals, values)
                if value <= threshold
            ]
            right = [
                residual
                for residual, value in zip(residuals, values)
                if value > threshold
            ]
            if not left or not right:
                continue
            left_value = sum(left) / len(left)
            right_value = sum(right) / len(right)
            error = sum(
                (
                    residual
                    - (left_value if value <= threshold else right_value)
                )
                ** 2
                for residual, value in zip(residuals, values)
            )
            if error < best_error:
                best_error = error
                best = {
                    "feature": name,
                    "threshold": threshold,
                    "left": left_value,
                    "right": right_value,
                }
        if best is None:
            break
        stumps.append(best)
        name = str(best["feature"])
        threshold = float(best["threshold"])
        left_value = float(best["left"])
        right_value = float(best["right"])
        for idx, item in enumerate(rows):
            value = _training_feature(item, name, preserve_missing=False)
            predictions[idx] += learning_rate * (
                left_value if value <= threshold else right_value
            )

    return {
        "base": base,
        "stumps": stumps,
        "lr": learning_rate,
        "rounds": rounds,
    }


def _fit_current_head(rows: list[Any], side: str) -> dict[str, Any]:
    anchor = _fit_anchor_stumps(rows, side)
    if side == "floor":
        challenger = {
            "kind": "atr_median",
            "feature": "atr_14",
            "multiplier": _atr_multiplier(rows, "floor"),
            "quantile": 0.5,
            "rows": len(rows),
            "objective": "median_absolute_error",
        }
    else:
        challenger = fit_ceiling_head(rows)
    return build_anchored_blend(anchor, challenger)


def _fit_residual_hgb(
    rows: list[Any],
    target: list[float],
) -> dict[str, Any]:
    try:
        import numpy as np
        from sklearn.ensemble import HistGradientBoostingRegressor
    except ImportError as exc:  # pragma: no cover - Actions installs modeling deps
        raise RuntimeError(
            "central_skill_ensemble_v1 training requires modeling dependencies"
        ) from exc

    x = np.asarray(
        _matrix(rows, CENTRAL_SKILL_FEATURES, preserve_missing=True),
        dtype=np.float64,
    )
    x = np.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0)
    y = np.asarray(target, dtype=np.float64)
    model = HistGradientBoostingRegressor(**RESIDUAL_HGB_PARAMS).fit(x, y)
    return export_hist_gradient_boosting(model, CENTRAL_SKILL_FEATURES)


def fit_central_skill_pair(
    rows: list[Any],
    horizon: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fit the frozen strict-validation winner for D1/W1 central forecasts."""

    if horizon not in SHRINKAGE_LAMBDA:
        raise ValueError(
            "central_skill_ensemble_v1 is validated only for d1 and w1"
        )
    if not rows:
        raise ValueError("central_skill_ensemble_v1 requires training rows")

    floor_k = _atr_multiplier(rows, "floor")
    ceiling_k = _atr_multiplier(rows, "ceiling")
    atr = [abs(float(item.features.get("atr_14", 0.0))) for item in rows]

    width = [
        float(item.floor_delta) + float(item.ceiling_delta)
        for item in rows
    ]
    asymmetry = [
        (
            float(item.ceiling_delta) - float(item.floor_delta)
        )
        / max(current_width, 1e-6)
        for item, current_width in zip(rows, width)
    ]
    width_target = [
        math.log(max(current_width / max(current_atr, 1e-6), 1e-4))
        for current_width, current_atr in zip(width, atr)
    ]
    width_model = _fit_ridge(rows, width_target)
    asymmetry_model = _fit_ridge(
        rows,
        [max(-0.95, min(0.95, value)) for value in asymmetry],
    )

    floor_base = [_clip(floor_k * value) for value in atr]
    ceiling_base = [_clip(ceiling_k * value) for value in atr]
    floor_residual = [
        float(item.floor_delta) - base
        for item, base in zip(rows, floor_base)
    ]
    ceiling_residual = [
        float(item.ceiling_delta) - base
        for item, base in zip(rows, ceiling_base)
    ]
    floor_residual_hgb = _fit_residual_hgb(rows, floor_residual)
    ceiling_residual_hgb = _fit_residual_hgb(rows, ceiling_residual)

    floor_current = _fit_current_head(rows, "floor")
    ceiling_current = _fit_current_head(rows, "ceiling")
    shrinkage_lambda = SHRINKAGE_LAMBDA[horizon]

    shared = {
        "kind": "central_skill_ensemble_v1",
        "schema_version": 1,
        "evidence": STRICT_VALIDATION_EVIDENCE,
        "weights": dict(ENSEMBLE_WEIGHTS),
        "spread_asymmetry": {
            "width_log_atr_ratio": width_model,
            "asymmetry": asymmetry_model,
        },
        "shrinkage_lambda": shrinkage_lambda,
        "training_contract": {
            "hyperparameters_frozen": True,
            "test_used_for_selection": False,
            "risk_geometry_separate": True,
            "coverage_used_for_directional_confidence": False,
        },
    }

    floor_params = {
        **shared,
        "side": "floor",
        "atr_multiplier": floor_k,
        "residual_hgb": floor_residual_hgb,
        "current_head": floor_current,
    }
    ceiling_params = {
        **shared,
        "side": "ceiling",
        "atr_multiplier": ceiling_k,
        "residual_hgb": ceiling_residual_hgb,
        "current_head": ceiling_current,
    }
    validate_central_skill_head(floor_params)
    validate_central_skill_head(ceiling_params)
    return floor_params, ceiling_params


def _spread_asymmetry_component(
    params: dict[str, Any],
    features: dict[str, float],
) -> float:
    spread = params["spread_asymmetry"]
    width_log_ratio = _predict_ridge(
        spread["width_log_atr_ratio"],
        features,
    )
    width_log_ratio = max(-10.0, min(10.0, width_log_ratio))
    atr = max(abs(float(features.get("atr_14", 0.0))), 1e-6)
    width = math.exp(width_log_ratio) * atr
    asymmetry = max(
        -0.95,
        min(0.95, _predict_ridge(spread["asymmetry"], features)),
    )
    if params["side"] == "floor":
        return _clip(width * (1.0 - asymmetry) / 2.0)
    return _clip(width * (1.0 + asymmetry) / 2.0)


def _residual_component(
    params: dict[str, Any],
    features: dict[str, float],
) -> float:
    atr = abs(float(features.get("atr_14", 0.0)))
    base = _clip(float(params["atr_multiplier"]) * atr)
    residual = predict_robust_head(
        params["residual_hgb"],
        features,
        validate=False,
    )
    return _clip(base + residual)


def _shrinkage_component(
    params: dict[str, Any],
    features: dict[str, float],
) -> float:
    atr = abs(float(features.get("atr_14", 0.0)))
    atr_prediction = _clip(float(params["atr_multiplier"]) * atr)
    current_prediction = _clip(
        predict_robust_head(
            params["current_head"],
            features,
            validate=False,
        )
    )
    lam = float(params["shrinkage_lambda"])
    return _clip(
        (1.0 - lam) * atr_prediction + lam * current_prediction
    )


def predict_central_skill_head(
    params: dict[str, Any],
    features: dict[str, float],
    *,
    validate: bool = True,
) -> float:
    if validate:
        validate_central_skill_head(params)
    components = {
        "spread_asymmetry": _spread_asymmetry_component(params, features),
        "residual_boosted": _residual_component(params, features),
        "atr_model_shrinkage": _shrinkage_component(params, features),
    }
    weights = params["weights"]
    prediction = sum(
        float(weights[name]) * value
        for name, value in components.items()
    )
    return _clip(prediction)


def validate_central_skill_head(params: dict[str, Any]) -> None:
    if params.get("kind") != "central_skill_ensemble_v1":
        raise ValueError("Invalid central skill head kind")
    if params.get("side") not in {"floor", "ceiling"}:
        raise ValueError("Central skill head requires side=floor|ceiling")
    if params.get("evidence") != STRICT_VALIDATION_EVIDENCE:
        raise ValueError("Central skill head missing frozen evidence identifier")

    weights = params.get("weights")
    if not isinstance(weights, dict):
        raise ValueError("Central skill head requires weights")
    if set(weights) != set(ENSEMBLE_WEIGHTS):
        raise ValueError("Central skill ensemble component set mismatch")
    total = 0.0
    for value in weights.values():
        if not isinstance(value, (int, float)) or float(value) < 0.0:
            raise ValueError("Central skill ensemble weights must be non-negative")
        total += float(value)
    if abs(total - 1.0) > 1e-9:
        raise ValueError("Central skill ensemble weights must sum to one")

    if not isinstance(params.get("atr_multiplier"), (int, float)):
        raise ValueError("Central skill head requires ATR multiplier")
    lam = params.get("shrinkage_lambda")
    if not isinstance(lam, (int, float)) or not 0.0 <= float(lam) <= 1.0:
        raise ValueError("Central skill head requires valid shrinkage lambda")

    spread = params.get("spread_asymmetry")
    if not isinstance(spread, dict):
        raise ValueError("Central skill head missing spread/asymmetry")
    _validate_ridge(spread.get("width_log_atr_ratio", {}))
    _validate_ridge(spread.get("asymmetry", {}))

    residual = params.get("residual_hgb")
    current = params.get("current_head")
    if not isinstance(residual, dict) or not isinstance(current, dict):
        raise ValueError("Central skill head missing residual/current component")
    validate_robust_head(residual)
    validate_robust_head(current)

    contract = params.get("training_contract")
    if not isinstance(contract, dict):
        raise ValueError("Central skill head missing training contract")
    if contract.get("test_used_for_selection") is not False:
        raise ValueError("Central skill head cannot use test for selection")
    if contract.get("risk_geometry_separate") is not True:
        raise ValueError("Central skill head must keep risk geometry separate")
    if contract.get("coverage_used_for_directional_confidence") is not False:
        raise ValueError("Coverage cannot be used as directional confidence")
