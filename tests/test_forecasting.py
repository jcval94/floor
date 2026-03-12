from __future__ import annotations

from datetime import datetime, timezone

from forecasting.merge_ai_signal import ai_recency_weight, merge_market_with_ai_signal
from forecasting.run_forecast import run_forecast_pipeline


def _market_rows() -> list[dict]:
    return [
        {
            "symbol": "AAPL",
            "close": 190.0,
            "high": 191.2,
            "low": 188.9,
            "atr_14": 2.1,
            "vol_regime_score": 1.1,
            "rel_strength_20": 0.03,
            "momentum_20": 0.05,
        },
        {
            "symbol": "MSFT",
            "close": 420.0,
            "high": 421.3,
            "low": 418.8,
            "atr_14": 3.0,
            "vol_regime_score": 0.9,
            "rel_strength_20": -0.01,
            "momentum_20": -0.02,
        },
        {"symbol": "TSLA", "close": None, "high": 200.0, "low": 190.0},
    ]


def _ai_map() -> dict[str, dict]:
    return {
        "AAPL": {
            "symbol": "AAPL",
            "ai_action": "BUY",
            "ai_conviction": 0.8,
            "ai_consensus_score": 0.7,
            "ai_updated_at": "2024-04-01T12:00:00+00:00",
        },
        "MSFT": {
            "symbol": "MSFT",
            "ai_action": "HOLD",
            "ai_conviction": 0.55,
            "ai_consensus_score": 0.2,
            "ai_recency": 9,
        },
    }


def test_ai_recency_weight_decreases_when_stale() -> None:
    assert ai_recency_weight(1) > ai_recency_weight(6)
    assert ai_recency_weight(10) <= 0.35


def test_merge_ai_signal_builds_effective_score() -> None:
    row = {"symbol": "AAPL", "close": 190}
    merged = merge_market_with_ai_signal(
        row,
        {"ai_conviction": 0.8, "ai_consensus_score": 0.7, "ai_recency": 1},
        as_of=datetime(2024, 4, 2, tzinfo=timezone.utc),
    )
    assert merged["ai_weight"] == 1.0
    assert merged["ai_effective_score"] > 0


def test_run_forecast_pipeline_outputs_required_shapes() -> None:
    out = run_forecast_pipeline(
        market_rows=_market_rows(),
        ai_by_symbol=_ai_map(),
        session="OPEN_PLUS_2H",
        as_of=datetime(2024, 4, 2, 14, 0, tzinfo=timezone.utc),
    )

    assert len(out["dataset_forecasts"]) == 2
    assert len(out["blocked_list"]) == 1
    row = out["dataset_forecasts"][0]

    required = [
        "floor_d1",
        "ceiling_d1",
        "floor_time_bucket_d1",
        "ceiling_time_bucket_d1",
        "breach_prob_d1",
        "expected_return_d1",
        "expected_range_d1",
        "floor_w1",
        "ceiling_w1",
        "floor_day_w1",
        "ceiling_day_w1",
        "breach_prob_w1",
        "expected_return_w1",
        "expected_range_w1",
        "floor_q1",
        "ceiling_q1",
        "floor_day_q1",
        "ceiling_day_q1",
        "breach_prob_q1",
        "expected_return_q1",
        "expected_range_q1",
        "confidence_score",
        "ai_alignment_score",
        "composite_signal_score",
        "reward_risk_ratio",
        "explanation_compact",
        "floor_date_w1",
        "ceiling_day_name_w1",
        "floor_date_q1",
        "ceiling_day_name_q1",
    ]
    for c in required:
        assert c in row

    assert out["top_opportunities"]
    assert isinstance(out["low_confidence_list"], list)
    assert out["canonical_strategy_output"]
    assert out["human_friendly_dashboard"]
