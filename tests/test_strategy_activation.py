from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from strategies.activation import activation_snapshot, strategy_activation_decision
from strategies.run_strategies import load_simple_yaml, run_strategies
from strategies.strategy_pack_v2 import generate_weekly_opportunity_orders


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


def test_research_remains_available_for_rebuilt_pack() -> None:
    cfg = _config()
    backtest = activation_snapshot(cfg, "backtest")

    assert set(backtest) == {
        "weekly_opportunity_ridge",
        "breakout_protected_by_floor",
        "mean_reversion_floor_w1",
        "cross_horizon_asymmetry",
    }
    assert all(item["allowed"] is True for item in backtest.values())
    assert all(item["canonical_serving_enabled"] is False for item in backtest.values())
    assert all(item["promotion_eligible"] is False for item in backtest.values())


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
            "floor_d1": 180.0,
            "ceiling_d1": 210.0,
            "floor_w1": 175.0,
            "ceiling_w1": 220.0,
            "floor_q1": 165.0,
            "ceiling_q1": 235.0,
            "confidence_score": 0.9,
            "momentum_20": 0.05,
            "rel_strength_20": 0.03,
            "avg_dollar_volume": 50_000_000,
            "floor_m3": 160.0,
            "floor_week_m3": 8,
            "floor_week_m3_confidence": 0.7,
            "weekly_opportunity_score": 1.5,
        }
    ]

    out = run_strategies(rows, cfg, session="OPEN_PLUS_2H", mode="paper")

    assert out["mode"] == "paper"
    assert out["n_signals"] == 0
    assert out["n_candidates"] == 0
    assert out["orders"] == []


def test_weekly_adapter_emits_buy_sell_and_hold() -> None:
    cfg = _config()
    strategy_cfg = cfg["strategies"]["weekly_opportunity_ridge"]
    rows = [
        {
            "symbol": "BUY",
            "close": 100.0,
            "floor_q1": 90.0,
            "ceiling_q1": 125.0,
            "avg_dollar_volume": 50_000_000,
            "weekly_opportunity_score": 2.0,
        },
        {
            "symbol": "MID1",
            "close": 100.0,
            "floor_q1": 90.0,
            "ceiling_q1": 120.0,
            "avg_dollar_volume": 50_000_000,
            "weekly_opportunity_score": 0.5,
        },
        {
            "symbol": "MID2",
            "close": 100.0,
            "floor_q1": 90.0,
            "ceiling_q1": 120.0,
            "avg_dollar_volume": 50_000_000,
            "weekly_opportunity_score": 0.0,
        },
        {
            "symbol": "MID3",
            "close": 100.0,
            "floor_q1": 90.0,
            "ceiling_q1": 120.0,
            "avg_dollar_volume": 50_000_000,
            "weekly_opportunity_score": -0.5,
        },
        {
            "symbol": "SELL",
            "close": 100.0,
            "floor_q1": 75.0,
            "ceiling_q1": 110.0,
            "avg_dollar_volume": 50_000_000,
            "weekly_opportunity_score": -2.0,
        },
    ]

    signals = generate_weekly_opportunity_orders(rows, cfg, strategy_cfg, session="CLOSE")
    actions = {signal.symbol: signal.side for signal in signals}

    assert actions["BUY"] == "BUY"
    assert actions["SELL"] == "SELL"
    assert actions["MID1"] == "HOLD"
    assert actions["MID2"] == "HOLD"
    assert actions["MID3"] == "HOLD"
