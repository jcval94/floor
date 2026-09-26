from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from models.central_skill_ensemble import (
    CENTRAL_SKILL_FEATURES,
    fit_central_skill_pair,
    predict_central_skill_head,
    training_feature_payload,
)
from models.classic_horizon_predictor import (
    build_runtime_features,
    model_family,
    predict_family_delta,
    validate_family_params,
)
from models.robust_range_v3 import ROBUST_RANGE_FEATURES
from models.train_classic_horizons import _prepare_rows


def _rows(n: int = 240) -> list[dict]:
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    rows: list[dict] = []
    for idx in range(n):
        close = 100.0 + 0.04 * idx
        atr_norm = 0.015 + 0.00003 * (idx % 40)
        momentum = (idx % 21 - 10) / 500.0
        floor_delta = 0.48 * atr_norm + 0.08 * max(-momentum, 0.0)
        ceiling_delta = 0.62 * atr_norm + 0.07 * max(momentum, 0.0)
        ts = start + timedelta(days=idx)
        row = {
            "timestamp": ts.isoformat(),
            "symbol": "AAA",
            "close": close,
            "open": close * (1.0 - 0.001),
            "high": close * 1.01,
            "low": close * 0.99,
            "atr_14": close * atr_norm,
            "floor_d1": close * (1.0 - floor_delta),
            "ceiling_d1": close * (1.0 + ceiling_delta),
            "rolling_vol_5": 0.010 + (idx % 5) * 0.0005,
            "rolling_vol_20": 0.012 + (idx % 7) * 0.0004,
            "rolling_vol_60": 0.014 + (idx % 9) * 0.0003,
            "downside_vol_20": 0.009 + (idx % 4) * 0.0004,
            "parkinson_vol_20": 0.02 + (idx % 6) * 0.0005,
            "gap_open_to_prev_close": (idx % 5 - 2) / 1000.0,
            "relative_volume_20": 0.9 + (idx % 8) * 0.03,
            "dist_to_low_20": 0.03 + (idx % 10) * 0.002,
            "dist_to_high_20": 0.04 + (idx % 9) * 0.002,
            "sma_slope_5_20": momentum / 2.0,
            "beta_20": 0.8 + (idx % 10) * 0.04,
            "rel_strength_20": momentum,
            "momentum_10": momentum * 0.7,
            "momentum_20": momentum,
            "vol_regime_score": 0.8 + (idx % 12) * 0.04,
            "recent_drawdown_20": -abs(momentum),
            "intraday_range_5": 0.02 + (idx % 5) * 0.001,
            "range_width_5": 0.04 + (idx % 5) * 0.002,
            "range_width_20": 0.08 + (idx % 7) * 0.003,
            "range_width_60": 0.13 + (idx % 8) * 0.004,
            "price_position_in_range_20": (idx % 20) / 19.0,
            "trend_context_m3": momentum * 2.0,
            "slope_4w": momentum,
            "slope_8w": momentum * 1.2,
            "slope_13w": momentum * 1.4,
            "drawdown_13w": -abs(momentum) * 1.5,
            "range_compression_20_60": 0.6 + (idx % 6) * 0.03,
            "rel_strength_4w": momentum,
            "rel_strength_8w": momentum * 1.1,
            "rel_strength_13w": momentum * 1.2,
            "dist_to_low_3m": 0.05 + (idx % 10) * 0.004,
            "dist_to_low_6m": 0.08 + (idx % 10) * 0.005,
            "dist_to_low_12m": 0.12 + (idx % 10) * 0.006,
            "vol_persistence_20_60": 0.8 + (idx % 8) * 0.04,
            "range_amp_daily_5": 0.02 + (idx % 5) * 0.001,
            "range_amp_daily_13": 0.025 + (idx % 7) * 0.001,
            "rsi_14": 40.0 + (idx % 20),
            "bollinger_width_20": 0.05 + (idx % 10) * 0.002,
            "vwap_distance": (idx % 9 - 4) / 1000.0,
            "ai_horizon_alignment": 0.0,
        }
        rows.append(row)
    return rows


def test_central_skill_ensemble_train_and_serving_parity() -> None:
    feature_names = tuple(
        sorted(
            set(CENTRAL_SKILL_FEATURES)
            | set(ROBUST_RANGE_FEATURES)
            | {
                "ai_horizon_alignment",
                "rel_strength_20",
            }
        )
    )
    prepared = _prepare_rows(
        _rows(),
        "floor_d1",
        "ceiling_d1",
        feature_names,
    )
    floor_params, ceiling_params = fit_central_skill_pair(prepared, "d1")

    validate_family_params("central_skill_ensemble_v1", floor_params)
    validate_family_params("central_skill_ensemble_v1", ceiling_params)
    assert model_family("central_skill_ensemble_v1_d1") == "central_skill_ensemble_v1"

    item = prepared[-1]
    training_features = training_feature_payload(item)
    runtime_features = build_runtime_features(item.row)

    train_floor = predict_central_skill_head(
        floor_params,
        training_features,
        validate=False,
    )
    serve_floor = predict_family_delta(
        "central_skill_ensemble_v1",
        floor_params,
        runtime_features,
    )
    train_ceiling = predict_central_skill_head(
        ceiling_params,
        training_features,
        validate=False,
    )
    serve_ceiling = predict_family_delta(
        "central_skill_ensemble_v1",
        ceiling_params,
        runtime_features,
    )

    assert serve_floor == pytest.approx(train_floor, rel=0, abs=1e-12)
    assert serve_ceiling == pytest.approx(train_ceiling, rel=0, abs=1e-12)
    assert floor_params["training_contract"]["test_used_for_selection"] is False
    assert floor_params["training_contract"]["risk_geometry_separate"] is True
    assert (
        floor_params["training_contract"]["coverage_used_for_directional_confidence"]
        is False
    )


def test_central_skill_ensemble_is_not_available_for_q1() -> None:
    feature_names = tuple(sorted(set(CENTRAL_SKILL_FEATURES) | set(ROBUST_RANGE_FEATURES)))
    prepared = _prepare_rows(
        _rows(20),
        "floor_d1",
        "ceiling_d1",
        feature_names,
    )
    with pytest.raises(ValueError, match="only for d1 and w1"):
        fit_central_skill_pair(prepared, "q1")
