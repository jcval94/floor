"""Robust, serializable floor/ceiling heads for the classic horizons.

The floor head is a structural median of realized downside excursion measured
in ATR units.  The ceiling head is a shallow histogram gradient booster using
absolute error.  Training depends on scikit-learn, while serving executes the
exported trees with this small pure-Python interpreter.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Iterable


ROBUST_RANGE_FEATURES: tuple[str, ...] = (
    "ret_lag_1",
    "ret_lag_2",
    "ret_lag_5",
    "ret_lag_10",
    "rolling_vol_5",
    "rolling_vol_20",
    "rolling_vol_60",
    "downside_vol_20",
    "atr_14",
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
    "intraday_range_5",
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
    "open_to_close",
    "high_to_close",
    "low_to_close",
    "month_sin",
    "month_cos",
)

HGB_PARAMS: dict[str, float | int | bool | str] = {
    "loss": "absolute_error",
    "learning_rate": 0.05,
    "max_iter": 80,
    "max_leaf_nodes": 7,
    "min_samples_leaf": 60,
    "l2_regularization": 0.1,
    "early_stopping": False,
    "random_state": 102,
}

CHALLENGER_WEIGHT = 0.20


def _number(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool) or value is None:
        return default
    if not isinstance(value, (str, int, float)):
        return default
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def feature_value(row: dict[str, Any], name: str, close: float) -> float:
    """Return one training/serving feature under a shared normalization contract."""

    denominator = max(close, 1e-6)
    if name == "atr_14":
        return _number(row.get(name)) / denominator
    if name == "open_to_close":
        return _number(row.get("open"), close) / denominator - 1.0
    if name == "high_to_close":
        return _number(row.get("high"), close) / denominator - 1.0
    if name == "low_to_close":
        return _number(row.get("low"), close) / denominator - 1.0
    if name in {"month_sin", "month_cos"}:
        raw = str(row.get("timestamp") or "")
        try:
            month = datetime.fromisoformat(raw).month
        except ValueError:
            month = 1
        angle = 2.0 * math.pi * month / 12.0
        return math.sin(angle) if name == "month_sin" else math.cos(angle)
    return _number(row.get(name))


def fit_floor_head(rows: Iterable[Any]) -> dict[str, Any]:
    """Fit the MAE-optimal downside excursion in normalized ATR units."""

    ratios = sorted(
        float(item.floor_delta) / max(float(item.features.get("atr_14", 0.0)), 1e-5)
        for item in rows
    )
    if not ratios:
        raise ValueError("robust_range_v3 floor head requires training rows")
    midpoint = len(ratios) // 2
    if len(ratios) % 2:
        multiplier = ratios[midpoint]
    else:
        multiplier = (ratios[midpoint - 1] + ratios[midpoint]) / 2.0
    return {
        "kind": "atr_median",
        "feature": "atr_14",
        "multiplier": multiplier,
        "quantile": 0.5,
        "rows": len(ratios),
        "objective": "median_absolute_error",
    }


def fit_ceiling_head(rows: list[Any]) -> dict[str, Any]:
    """Fit and export the robust ceiling model; sklearn is training-only."""

    if not rows:
        raise ValueError("robust_range_v3 ceiling head requires training rows")
    try:
        import numpy as np
        from sklearn.ensemble import HistGradientBoostingRegressor
    except ImportError as exc:  # pragma: no cover - exercised by the Actions setup
        raise RuntimeError(
            "robust_range_v3 training requires the 'modeling' optional dependencies"
        ) from exc

    matrix = np.asarray(
        [
            [float(item.features.get(name, 0.0)) for name in ROBUST_RANGE_FEATURES]
            for item in rows
        ],
        dtype=np.float64,
    )
    targets = np.asarray([float(item.ceiling_delta) for item in rows], dtype=np.float64)
    model = HistGradientBoostingRegressor(**HGB_PARAMS).fit(matrix, targets)
    return export_hist_gradient_boosting(model, ROBUST_RANGE_FEATURES)


def export_hist_gradient_boosting(
    model: Any,
    feature_names: tuple[str, ...],
) -> dict[str, Any]:
    """Export sklearn's fitted numeric trees to a stable, compact JSON contract."""

    baseline_raw = getattr(model, "_baseline_prediction", None)
    predictors = getattr(model, "_predictors", None)
    if baseline_raw is None or not isinstance(predictors, list):
        raise RuntimeError("Unsupported sklearn HistGradientBoosting model internals")

    trees: list[list[list[float | int]]] = []
    for stage in predictors:
        if len(stage) != 1:
            raise RuntimeError("Only scalar HistGradientBoosting regressors are supported")
        exported_nodes: list[list[float | int]] = []
        for node in stage[0].nodes:
            if bool(node["is_categorical"]):
                raise RuntimeError("Categorical HistGradientBoosting nodes are unsupported")
            exported_nodes.append(
                [
                    int(node["is_leaf"]),
                    int(node["feature_idx"]),
                    float(node["num_threshold"]),
                    int(node["left"]),
                    int(node["right"]),
                    float(node["value"]),
                ]
            )
        trees.append(exported_nodes)

    return {
        "kind": "hist_gradient_boosting",
        "features": list(feature_names),
        "baseline": float(baseline_raw[0][0]),
        "trees": trees,
        "imputation": "zero",
        "objective": "absolute_error",
        "hyperparameters": dict(HGB_PARAMS),
        "sklearn_version": str(getattr(__import__("sklearn"), "__version__", "unknown")),
    }


def validate_head(params: dict[str, Any]) -> None:
    kind = str(params.get("kind") or "")
    if kind == "anchored_blend":
        weight = _number(params.get("challenger_weight"), -1.0)
        if not 0.0 < weight < 1.0:
            raise ValueError("Anchored blend requires challenger_weight in (0, 1)")
        anchor = params.get("anchor")
        challenger = params.get("challenger")
        if not isinstance(anchor, dict) or not isinstance(challenger, dict):
            raise ValueError("Anchored blend requires anchor and challenger mappings")
        _validate_boosted_stumps(anchor)
        validate_head(challenger)
        return

    if kind == "atr_median":
        if params.get("feature") != "atr_14":
            raise ValueError("ATR-median head requires feature=atr_14")
        multiplier = _number(params.get("multiplier"), -1.0)
        if multiplier <= 0.0:
            raise ValueError("ATR-median head requires a positive multiplier")
        if _number(params.get("quantile"), -1.0) != 0.5:
            raise ValueError("ATR-median head requires quantile=0.5")
        return

    if kind != "hist_gradient_boosting":
        raise ValueError(f"Unsupported robust_range_v3 head kind: {kind or 'missing'}")
    features = params.get("features")
    trees = params.get("trees")
    if not isinstance(features, list) or not features or not all(
        isinstance(name, str) and name for name in features
    ):
        raise ValueError("Histogram booster requires a non-empty feature list")
    if not isinstance(params.get("baseline"), (int, float)):
        raise ValueError("Histogram booster requires a numeric baseline")
    if not isinstance(trees, list) or not trees:
        raise ValueError("Histogram booster requires at least one tree")
    for tree in trees:
        if not isinstance(tree, list) or not tree:
            raise ValueError("Histogram booster tree must be a non-empty list")
        for node in tree:
            if not isinstance(node, list) or len(node) != 6:
                raise ValueError("Histogram booster node must contain six values")
            if not all(isinstance(value, (int, float)) for value in node):
                raise ValueError("Histogram booster node values must be numeric")
            is_leaf, feature_idx, _threshold, left, right, _value = node
            if int(is_leaf) not in {0, 1}:
                raise ValueError("Histogram booster node leaf flag is invalid")
            if not 0 <= int(feature_idx) < len(features):
                raise ValueError("Histogram booster feature index is out of bounds")
            if not bool(is_leaf) and not (
                0 <= int(left) < len(tree) and 0 <= int(right) < len(tree)
            ):
                raise ValueError("Histogram booster child index is out of bounds")


def predict_head(
    params: dict[str, Any],
    features: dict[str, float],
    *,
    validate: bool = True,
) -> float:
    """Execute either robust head from its serialized representation."""

    if validate:
        validate_head(params)
    if params["kind"] == "anchored_blend":
        weight = float(params["challenger_weight"])
        anchor = _predict_boosted_stumps(params["anchor"], features)
        challenger = predict_head(params["challenger"], features, validate=False)
        return (1.0 - weight) * anchor + weight * challenger
    if params["kind"] == "atr_median":
        return float(params["multiplier"]) * float(features.get("atr_14", 0.0))

    names = [str(name) for name in params["features"]]
    values = [float(features.get(name, 0.0)) for name in names]
    prediction = float(params["baseline"])
    for tree in params["trees"]:
        node_index = 0
        while True:
            is_leaf, feature_idx, threshold, left, right, value = tree[node_index]
            if bool(is_leaf):
                prediction += float(value)
                break
            node_index = (
                int(left)
                if values[int(feature_idx)] <= float(threshold)
                else int(right)
            )
    return prediction


def build_anchored_blend(
    anchor: dict[str, Any],
    challenger: dict[str, Any],
) -> dict[str, Any]:
    """Wrap the new head with a conservative, coverage-preserving anchor."""

    params = {
        "kind": "anchored_blend",
        "anchor_family": "boosted_stumps",
        "anchor": anchor,
        "challenger": challenger,
        "challenger_weight": CHALLENGER_WEIGHT,
        "selection_reason": (
            "largest fixed challenger share pre-registered to keep validation "
            "interval-coverage regression within five percentage points"
        ),
    }
    validate_head(params)
    return params


def _validate_boosted_stumps(params: dict[str, Any]) -> None:
    if not isinstance(params.get("base"), (int, float)) or not isinstance(
        params.get("lr"), (int, float)
    ):
        raise ValueError("Anchored boosted stumps require numeric base and lr")
    stumps = params.get("stumps")
    if not isinstance(stumps, list):
        raise ValueError("Anchored boosted stumps require a stump list")
    for stump in stumps:
        if not isinstance(stump, dict) or not str(stump.get("feature") or ""):
            raise ValueError("Anchored boosted stump is malformed")
        if not all(
            isinstance(stump.get(key), (int, float))
            for key in ("threshold", "left", "right")
        ):
            raise ValueError("Anchored boosted stump values must be numeric")


def _predict_boosted_stumps(
    params: dict[str, Any],
    features: dict[str, float],
) -> float:
    prediction = float(params["base"])
    learning_rate = float(params["lr"])
    for stump in params["stumps"]:
        value = float(features.get(str(stump["feature"]), 0.0))
        leaf = stump["left"] if value <= float(stump["threshold"]) else stump["right"]
        prediction += learning_rate * float(leaf)
    return prediction
