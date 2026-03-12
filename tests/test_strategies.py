from __future__ import annotations

from pathlib import Path

from strategies.run_strategies import load_simple_yaml, run_strategies


def _rows() -> list[dict]:
    return [
        {
            "symbol": "AAPL",
            "sector": "Technology",
            "close": 190.0,
            "floor_d1": 186.0,
            "ceiling_d1": 198.0,
            "floor_time_bucket_d1": "OPEN_PLUS_2H",
            "ceiling_time_bucket_d1": "OPEN_PLUS_4H",
            "breach_prob_d1": 0.35,
            "expected_return_d1": 0.012,
            "expected_range_d1": 12.0,
            "floor_w1": 187.0,
            "ceiling_w1": 205.0,
            "floor_day_w1": 2,
            "ceiling_day_w1": 5,
            "breach_prob_w1": 0.45,
            "expected_return_w1": 0.018,
            "expected_range_w1": 23.0,
            "floor_q1": 175.0,
            "ceiling_q1": 212.0,
            "floor_day_q1": 3,
            "ceiling_day_q1": 9,
            "breach_prob_q1": 0.5,
            "expected_return_q1": 0.025,
            "expected_range_q1": 37.0,
            "confidence_score": 0.72,
            "ai_alignment_score": 0.22,
            "composite_signal_score": 0.18,
            "reward_risk_ratio": 1.8,
            "momentum_20": 0.03,
            "avg_dollar_volume": 20000000,
            "floor_m3": 176.0,
            "floor_week_m3": 8,
            "floor_week_m3_confidence": 0.42,
            "expected_return_m3": 0.03,
        },
        {
            "symbol": "MSFT",
            "sector": "Technology",
            "close": 420.0,
            "floor_d1": 415.0,
            "ceiling_d1": 430.0,
            "floor_time_bucket_d1": "OPEN_PLUS_6H",
            "ceiling_time_bucket_d1": "CLOSE",
            "breach_prob_d1": 0.60,
            "expected_return_d1": 0.004,
            "expected_range_d1": 15.0,
            "floor_w1": 408.0,
            "ceiling_w1": 440.0,
            "floor_day_w1": 1,
            "ceiling_day_w1": 5,
            "breach_prob_w1": 0.52,
            "expected_return_w1": 0.01,
            "expected_range_w1": 32.0,
            "floor_q1": 395.0,
            "ceiling_q1": 455.0,
            "floor_day_q1": 2,
            "ceiling_day_q1": 10,
            "breach_prob_q1": 0.55,
            "expected_return_q1": 0.016,
            "expected_range_q1": 60.0,
            "confidence_score": 0.58,
            "ai_alignment_score": 0.09,
            "composite_signal_score": 0.07,
            "reward_risk_ratio": 1.3,
            "momentum_20": 0.015,
            "avg_dollar_volume": 15000000,
            "floor_m3": 380.0,
            "floor_week_m3": 2,
            "floor_week_m3_confidence": 0.66,
            "expected_return_m3": -0.02,
        },
        {
            "symbol": "TSLA",
            "sector": "Consumer",
            "close": 200.0,
            "floor_d1": 199.5,
            "ceiling_d1": 201.0,
            "floor_time_bucket_d1": "OPEN_PLUS_4H",
            "ceiling_time_bucket_d1": "OPEN_PLUS_6H",
            "breach_prob_d1": 0.4,
            "expected_return_d1": 0.003,
            "expected_range_d1": 1.5,
            "floor_w1": 190.0,
            "ceiling_w1": 214.0,
            "floor_day_w1": 2,
            "ceiling_day_w1": 5,
            "breach_prob_w1": 0.5,
            "expected_return_w1": 0.011,
            "expected_range_w1": 24.0,
            "floor_q1": 182.0,
            "ceiling_q1": 222.0,
            "floor_day_q1": 3,
            "ceiling_day_q1": 9,
            "breach_prob_q1": 0.52,
            "expected_return_q1": 0.014,
            "expected_range_q1": 40.0,
            "confidence_score": 0.62,
            "ai_alignment_score": 0.11,
            "composite_signal_score": 0.09,
            "reward_risk_ratio": 1.5,
            "momentum_20": 0.02,
            "avg_dollar_volume": 18000000,
            "floor_m3": 170.0,
            "floor_week_m3": 1,
            "floor_week_m3_confidence": 0.71,
            "expected_return_m3": -0.03,
        },
    ]


def test_run_strategies_generates_theoretical_orders() -> None:
    cfg = load_simple_yaml(Path("config/strategies.yaml"))
    out = run_strategies(_rows(), cfg, session="OPEN_PLUS_2H", cooldown_state={}, current_cycle=5)

    assert out["n_candidates"] > 0
    assert out["orders"]
    sample = out["orders"][0]
    for col in [
        "strategy_id",
        "symbol",
        "side",
        "score",
        "entry_reason",
        "stop_price",
        "take_profit_price",
        "cost_assumption_bps",
        "m3_context",
        "priority_adjustment",
    ]:
        assert col in sample


def test_collision_priority_is_explicit() -> None:
    cfg = load_simple_yaml(Path("config/strategies.yaml"))
    out = run_strategies(_rows(), cfg, session="OPEN_PLUS_2H", cooldown_state={}, current_cycle=1)

    symbols = [o["symbol"] for o in out["orders"]]
    assert len(symbols) == len(set(symbols))
    assert any(("collision" in b["reason"].lower()) or ("ticker limit" in b["reason"].lower()) for b in out["blocked"])


def test_narrow_range_blocks_trade_by_cost_guard() -> None:
    cfg = load_simple_yaml(Path("config/strategies.yaml"))
    rows = _rows()
    rows[0]["expected_range_d1"] = 0.01
    out = run_strategies(rows, cfg, session="OPEN_PLUS_2H", cooldown_state={}, current_cycle=10)

    # AAPL should be absent because expected range vs cost is too tight.
    assert all(order["symbol"] != "AAPL" for order in out["orders"])


def test_all_five_strategy_ids_can_appear_as_candidates() -> None:
    cfg = load_simple_yaml(Path("config/strategies.yaml"))
    out = run_strategies(_rows(), cfg, session="OPEN_PLUS_2H", cooldown_state={}, current_cycle=3)
    blocked_strategies = {b["strategy"] for b in out["blocked"]}
    order_strategies = {o["strategy_id"] for o in out["orders"]}
    # Because collision resolution keeps one strategy per ticker, verify coverage across orders+blocked.
    coverage = blocked_strategies | order_strategies
    expected = {
        "ai_only",
        "model_only",
        "consensus",
        "mean_reversion_floor_w1",
        "breakout_protected_by_floor",
    }
    assert expected.issubset(coverage)


def test_m3_context_reduces_sizing_when_floor_week_is_near() -> None:
    cfg = load_simple_yaml(Path("config/strategies.yaml"))
    rows = _rows()
    # Make one symbol clearly valid for model_only and with near-term m3 floor week.
    rows[0]["reward_risk_ratio"] = 2.2
    rows[0]["ai_alignment_score"] = 0.0
    rows[0]["floor_week_m3"] = 1
    rows[0]["floor_m3"] = 180.0
    out = run_strategies(rows, cfg, session="OPEN_PLUS_2H", cooldown_state={}, current_cycle=9)

    with_m3 = [o for o in out["orders"] if o["symbol"] == "AAPL" and o.get("m3_context", {}).get("enabled") is True]
    assert with_m3
    order = with_m3[0]
    assert order["m3_context"]["near_term_floor_week"] is True
    assert order["m3_context"]["size_multiplier"] < 1.0


def test_m3_context_blocks_tactical_long_when_floor_is_likely_ahead() -> None:
    cfg = load_simple_yaml(Path("config/strategies.yaml"))
    rows = _rows()
    rows[0]["close"] = 200.0
    rows[0]["floor_m3"] = 160.0
    rows[0]["floor_week_m3"] = 2
    rows[0]["reward_risk_ratio"] = 2.5
    out = run_strategies(rows, cfg, session="OPEN_PLUS_2H", cooldown_state={}, current_cycle=11)

    # AAPL longs should be filtered by m3 tactical block when still far above floor_m3 and floor likely ahead.
    assert all(order["symbol"] != "AAPL" for order in out["orders"])


def test_m3_context_documents_cross_horizon_contradiction() -> None:
    cfg = load_simple_yaml(Path("config/strategies.yaml"))
    rows = _rows()
    rows[1]["expected_return_d1"] = 0.008
    rows[1]["expected_return_w1"] = 0.012
    rows[1]["expected_return_q1"] = 0.02
    rows[1]["expected_return_m3"] = -0.03
    rows[1]["reward_risk_ratio"] = 2.3
    out = run_strategies(rows, cfg, session="OPEN_PLUS_2H", cooldown_state={}, current_cycle=12)

    msft = [o for o in out["orders"] if o["symbol"] == "MSFT" and o.get("m3_context", {}).get("enabled") is True]
    if msft:
        contrad = msft[0]["m3_context"].get("contradicts_horizons", [])
        assert "d1" in contrad or "w1" in contrad or "q1" in contrad
