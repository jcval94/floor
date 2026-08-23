from __future__ import annotations

import pytest

from floor.pipeline.intraday_cycle import (
    LEGACY_RUNTIME_ERROR,
    _fallback_forecasts_from_blocked,
    _prediction_payloads,
    _signal_from_prediction,
    _validate_prediction_payload,
    maybe_build_order,
    run_intraday_cycle,
)


def test_legacy_intraday_cycle_is_disabled() -> None:
    with pytest.raises(RuntimeError, match="Legacy intraday cycle is disabled"):
        run_intraday_cycle(event_type="OPEN", symbols=["AAPL"], cfg=object())


def test_legacy_order_builder_is_disabled() -> None:
    with pytest.raises(RuntimeError, match="direct qty=1 order creation"):
        maybe_build_order(object(), object())


def test_legacy_synthetic_fallback_is_disabled() -> None:
    with pytest.raises(RuntimeError, match="synthetic forecast fallback"):
        _fallback_forecasts_from_blocked([], [])


def test_legacy_error_explains_canonical_replacement() -> None:
    assert "canonical_intraday_cycle.run_intraday_cycle" in LEGACY_RUNTIME_ERROR


def test_signal_from_prediction_uses_model_confidence_not_composite_as_probability() -> None:
    signal = _signal_from_prediction(
        symbol="AAPL",
        horizon="d1",
        floor=95.0,
        ceiling=105.0,
        expected_return=0.03,
        confidence_score=0.20,
        composite_signal_score=0.99,
    )
    assert signal.action == "HOLD"
    assert signal.confidence == 0.20


def test_signal_from_prediction_buy_sell_hold_rules() -> None:
    buy = _signal_from_prediction("AAPL", "d1", 95.0, 105.0, 0.02, 0.8, None)
    sell = _signal_from_prediction("AAPL", "d1", 95.0, 105.0, -0.02, 0.8, None)
    hold = _signal_from_prediction("AAPL", "d1", 95.0, 105.0, 0.0, 0.8, None)
    assert buy.action == "BUY"
    assert sell.action == "SELL"
    assert hold.action == "HOLD"


def test_d1_unavailable_timing_stays_empty_not_string_none() -> None:
    row = {
        "floor_d1": 95.0,
        "ceiling_d1": 105.0,
        "floor_time_bucket_d1": None,
        "ceiling_time_bucket_d1": None,
        "breach_prob_d1": 0.2,
        "expected_return_d1": 0.01,
        "expected_range_d1": 10.0,
        "floor_w1": 90.0,
        "ceiling_w1": 110.0,
        "floor_day_w1": 2,
        "ceiling_day_w1": 5,
        "breach_prob_w1": 0.3,
        "expected_return_w1": 0.02,
        "expected_range_w1": 20.0,
        "floor_q1": 80.0,
        "ceiling_q1": 120.0,
        "floor_day_q1": 3,
        "ceiling_day_q1": 10,
        "breach_prob_q1": 0.4,
        "expected_return_q1": 0.03,
        "expected_range_q1": 40.0,
        "floor_m3": None,
        "floor_week_m3": None,
        "floor_week_m3_confidence": None,
        "floor_week_m3_top3": [],
        "m3_status": "blocked",
        "m3_block_reason": "not available",
    }
    payloads = dict(_prediction_payloads(row, event_type="OPEN"))
    d1 = payloads["d1"]
    assert d1["floor_time_bucket"] == ""
    assert d1["ceiling_time_bucket"] == ""
    assert d1["floor_time_probability"] == 0.0
    _validate_prediction_payload("AAPL", "d1", d1)


def test_q1_timing_domain_rejects_regression_above_ten() -> None:
    payload = {
        "floor_value": 90.0,
        "ceiling_value": 110.0,
        "floor_time_bucket": "3",
        "ceiling_time_bucket": "20",
        "confidence_score": 0.8,
        "expected_range": 20.0,
    }
    with pytest.raises(RuntimeError, match="q1.*1..10"):
        _validate_prediction_payload("AAPL", "q1", payload)
