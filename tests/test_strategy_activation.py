from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from strategies.activation import activation_snapshot, strategy_activation_decision
from strategies.run_strategies import load_simple_yaml, run_strategies
from strategies.strategy_weekly_opportunity import generate_weekly_opportunity_orders


def _config() -> dict:
    return load_simple_yaml(Path("config/strategies.yaml"))


def test_repository_config_keeps_all_execution_modes_off() -> None:
    cfg = _config()

    paper = activation_snapshot(cfg, "paper")
    live = activation_snapshot(cfg, "live")

    assert paper
    assert live
    assert all(item["allowed"] is False for item in paper.values())
    assert all(item["allowed"] is False for item in live.values())
    assert cfg["activation"]["paper_execution_enabled"] is False
    assert cfg["activation"]["live_execution_enabled"] is False


def test_research_remains_available_without_enabling_execution() -> None:
    cfg = _config()
    backtest = activation_snapshot(cfg, "backtest")

    assert backtest["weekly_opportunity_ridge"]["allowed"] is True
    assert backtest["weekly_opportunity_ridge"]["readiness"] == "challenger_waiting_out_of_sample"
    assert backtest["weekly_opportunity_ridge"]["canonical_serving_enabled"] is False
    assert backtest["weekly_opportunity_ridge"]["promotion_eligible"] is False


def test_paper_challenger_can_be_enabled_without_canonical_promotion() -> None:
    cfg = deepcopy(_config())
    cfg["activation"]["paper_execution_enabled"] = True
    cfg["strategies"]["weekly_opportunity_ridge"]["paper_enabled"] = True

    decision = strategy_activation_decision(cfg, "weekly_opportunity_ridge", "paper")

    assert decision.allowed is True
    assert decision.canonical_serving_enabled is False
    assert decision.promotion_eligible is False


def test_live_requires_global_strategy_and_canonical_gates() -> None:
    cfg = deepcopy(_config())
    strategy = cfg["strategies"]["weekly_opportunity_ridge"]
    cfg["activation"]["live_execution_enabled"] = True
    strategy["live_enabled"] = True

    blocked = strategy_activation_decision(cfg, "weekly_opportunity_ridge", "live")
    assert blocked.allowed is False
    assert blocked.reason == "canonical_serving_disabled"

    strategy["canonical_serving_enabled"] = True
    allowed = strategy_activation_decision(cfg, "weekly_opportunity_ridge", "live")
    assert allowed.allowed is True


def test_paper_runner_is_empty_with_repository_defaults() -> None:
    cfg = _config()
    rows = [
        {
            "symbol": "AAPL",
            "sector": "Technology",
            "close": 190.0,
            "floor_d1": 185.0,
            "ceiling_d1": 200.0,
            "expected_range_d1": 15.0,
            "ai_alignment_score": 0.9,
            "expected_return_d1": 0.02,
            "breach_prob_d1": 0.2,
            "composite_signal_score": 0.9,
            "confidence_score": 0.9,
            "momentum_20": 0.05,
            "avg_dollar_volume": 50_000_000,
            "floor_w1": 180.0,
            "ceiling_w1": 205.0,
            "expected_return_w1": 0.03,
            "floor_m3": 170.0,
            "floor_week_m3": 8,
            "floor_week_m3_confidence": 0.7,
            "reward_risk_ratio": 2.0,
            "expected_return_m3": 0.02,
            "floor_q1": 175.0,
            "ceiling_q1": 215.0,
            "weekly_opportunity_score": 1.5,
        }
    ]

    out = run_strategies(rows, cfg, session="OPEN_PLUS_2H", mode="paper")

    assert out["mode"] == "paper"
    assert out["n_candidates"] == 0
    assert out["orders"] == []


def test_weekly_adapter_matches_positive_top_fraction_contract() -> None:
    cfg = _config()
    strategy_cfg = cfg["strategies"]["weekly_opportunity_ridge"]
    rows = []
    for idx, score in enumerate([2.0, 1.0, 0.5, 0.1, -0.2]):
        rows.append(
            {
                "symbol": f"S{idx}",
                "close": 100.0,
                "floor_q1": 90.0,
                "ceiling_q1": 120.0,
                "avg_dollar_volume": 50_000_000,
                "weekly_opportunity_score": score,
            }
        )

    orders = generate_weekly_opportunity_orders(rows, cfg, strategy_cfg, session="CLOSE")

    # ceil(5 * 20%) = 1, and only positive scores are eligible.
    assert [order.symbol for order in orders] == ["S0"]
    assert orders[0].side == "BUY"
    assert orders[0].horizon == "q1"
