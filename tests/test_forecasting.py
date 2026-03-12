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
            "trend_context_m3": 0.04,
            "drawdown_13w": -0.02,
            "ai_horizon_alignment": 1.0,
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
            # Missing m3-specific features on purpose -> only m3 output should be blocked.
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
            "ai_horizon_alignment": 1.0,
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


def test_run_forecast_pipeline_outputs_required_shapes_and_m3() -> None:
    out = run_forecast_pipeline(
        market_rows=_market_rows(),
        ai_by_symbol=_ai_map(),
        session="OPEN_PLUS_2H",
        as_of=datetime(2024, 4, 2, 14, 0, tzinfo=timezone.utc),
    )

    assert len(out["dataset_forecasts"]) == 2
    assert len(out["blocked_list"]) == 1

    aapl = next(r for r in out["dataset_forecasts"] if r["symbol"] == "AAPL")
    msft = next(r for r in out["dataset_forecasts"] if r["symbol"] == "MSFT")

    required_base = [
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
    for c in required_base:
        assert c in aapl

    required_m3 = [
        "floor_m3",
        "floor_week_m3",
        "floor_week_m3_confidence",
        "floor_week_m3_top3",
        "floor_week_m3_start_date",
        "floor_week_m3_end_date",
        "floor_week_m3_label_human",
        "expected_return_m3",
        "expected_range_m3",
    ]
    for c in required_m3:
        assert c in aapl

    assert aapl["floor_week_m3"] in set(range(1, 14))
    assert len(aapl["floor_week_m3_top3"]) == 3
    assert aapl["m3_status"] == "ok"
    assert "1..13" in aapl["floor_week_m3_label_human"]

    # MSFT should remain in forecasts, but only m3 should be blocked.
    assert msft["m3_status"] == "blocked"
    assert msft["m3_block_reason"] is not None
    assert msft["floor_d1"] is not None

    assert out["top_opportunities"]
    assert isinstance(out["low_confidence_list"], list)
    assert out["canonical_strategy_output"]
    assert out["human_friendly_dashboard"]


def test_canonical_and_dashboard_include_m3_fields() -> None:
    out = run_forecast_pipeline(
        market_rows=_market_rows(),
        ai_by_symbol=_ai_map(),
        session="OPEN_PLUS_2H",
        as_of=datetime(2024, 4, 2, 14, 0, tzinfo=timezone.utc),
    )

    can = out["canonical_strategy_output"][0]
    dash = out["human_friendly_dashboard"][0]

    assert "floor_m3" in can
    assert "floor_week_m3" in can
    assert "floor_week_m3_top3" in can
    assert "m3_week_index" in dash
    assert "m3_week_start_date" in dash
    assert "m3_week_end_date" in dash
