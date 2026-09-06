from __future__ import annotations

from pathlib import Path

from strategies.run_strategies import load_simple_yaml, run_strategies


def _rows() -> list[dict]:
    return [
        {
            "symbol": "BUY",
            "sector": "Technology",
            "close": 100.0,
            "floor_d1": 96.0,
            "ceiling_d1": 110.0,
            "floor_w1": 92.0,
            "ceiling_w1": 116.0,
            "floor_q1": 88.0,
            "ceiling_q1": 125.0,
            "confidence_score": 0.80,
            "momentum_20": 0.040,
            "rel_strength_20": 0.030,
            "avg_dollar_volume": 50_000_000,
            "floor_m3": 80.0,
            "floor_week_m3": 8,
            "floor_week_m3_confidence": 0.70,
            "weekly_opportunity_score": 2.0,
        },
        {
            "symbol": "SELL",
            "sector": "Technology",
            "close": 100.0,
            "floor_d1": 90.0,
            "ceiling_d1": 104.0,
            "floor_w1": 84.0,
            "ceiling_w1": 108.0,
            "floor_q1": 75.0,
            "ceiling_q1": 112.0,
            "confidence_score": 0.80,
            "momentum_20": -0.040,
            "rel_strength_20": -0.030,
            "avg_dollar_volume": 50_000_000,
            "floor_m3": 80.0,
            "floor_week_m3": 1,
            "floor_week_m3_confidence": 0.80,
            "weekly_opportunity_score": -2.0,
        },
        {
            "symbol": "HOLD",
            "sector": "Technology",
            "close": 100.0,
            "floor_d1": 99.7,
            "ceiling_d1": 100.4,
            "floor_w1": 98.0,
            "ceiling_w1": 102.0,
            "floor_q1": 97.0,
            "ceiling_q1": 103.0,
            "confidence_score": 0.50,
            "momentum_20": 0.0,
            "rel_strength_20": 0.0,
            "avg_dollar_volume": 50_000_000,
            "floor_m3": 90.0,
            "floor_week_m3": 0,
            "floor_week_m3_confidence": 0.10,
            "weekly_opportunity_score": 0.0,
        },
    ]


def test_strategy_pack_outputs_buy_sell_hold_explicitly() -> None:
    cfg = load_simple_yaml(Path("config/strategies.yaml"))
    out = run_strategies(_rows(), cfg, session="OPEN_PLUS_2H")

    assert out["n_signals"] == 12
    assert out["action_counts"]["BUY"] > 0
    assert out["action_counts"]["SELL"] > 0
    assert out["action_counts"]["HOLD"] > 0
    assert all(signal["action"] in {"BUY", "SELL", "HOLD"} for signal in out["signals"])
    assert all(order["side"] in {"BUY", "SELL"} for order in out["orders"])


def test_cost_contract_includes_platform_fee_on_both_sides() -> None:
    cfg = load_simple_yaml(Path("config/strategies.yaml"))
    out = run_strategies(_rows(), cfg, session="OPEN_PLUS_2H")

    assert cfg["costs"]["platform_fee_bps_per_side"] == 24.0
    assert all(signal["platform_fee_bps_per_side"] == 24.0 for signal in out["signals"])
    assert all(signal["round_trip_cost_bps"] == 58.0 for signal in out["signals"])
    assert all(order["round_trip_cost_bps"] == 58.0 for order in out["orders"])


def test_legacy_directional_strategies_are_not_registered() -> None:
    cfg = load_simple_yaml(Path("config/strategies.yaml"))
    assert "ai_only" not in cfg["strategies"]
    assert "model_only" not in cfg["strategies"]
    assert "consensus" not in cfg["strategies"]
    assert set(cfg["strategies"]) == {
        "weekly_opportunity_ridge",
        "breakout_protected_by_floor",
        "mean_reversion_floor_w1",
        "cross_horizon_asymmetry",
    }


def test_narrow_d1_geometry_becomes_hold_instead_of_fake_trade() -> None:
    cfg = load_simple_yaml(Path("config/strategies.yaml"))
    row = _rows()[0]
    row["floor_d1"] = 99.8
    row["ceiling_d1"] = 100.3

    out = run_strategies([row], cfg, session="OPEN_PLUS_2H")
    breakout = next(
        signal
        for signal in out["signals"]
        if signal["strategy_id"] == "breakout_protected_by_floor"
    )
    assert breakout["action"] == "HOLD"


def test_mean_reversion_is_symmetric_at_w1_anchors() -> None:
    cfg = load_simple_yaml(Path("config/strategies.yaml"))
    buy = _rows()[0]
    buy.update(
        {
            "symbol": "MRBUY",
            "close": 100.0,
            "floor_w1": 99.0,
            "ceiling_w1": 112.0,
            "momentum_20": 0.0,
            "rel_strength_20": 0.0,
        }
    )
    sell = _rows()[1]
    sell.update(
        {
            "symbol": "MRSELL",
            "close": 100.0,
            "floor_w1": 88.0,
            "ceiling_w1": 101.0,
            "momentum_20": 0.0,
            "rel_strength_20": 0.0,
        }
    )

    out = run_strategies([buy, sell], cfg, session="OPEN_PLUS_2H")
    signals = {
        (signal["strategy_id"], signal["symbol"]): signal["action"]
        for signal in out["signals"]
    }
    assert signals[("mean_reversion_floor_w1", "MRBUY")] == "BUY"
    assert signals[("mean_reversion_floor_w1", "MRSELL")] == "SELL"


def test_m3_timing_only_blocks_buy_when_timing_is_reliable() -> None:
    cfg = load_simple_yaml(Path("config/strategies.yaml"))
    row = _rows()[0]
    row.update(
        {
            "symbol": "M3",
            "floor_m3": 80.0,
            "floor_week_m3": 1,
            "floor_week_m3_confidence": 0.90,
        }
    )
    out = run_strategies([row], cfg, session="OPEN_PLUS_2H")
    breakout = next(
        signal
        for signal in out["signals"]
        if signal["strategy_id"] == "breakout_protected_by_floor"
    )
    assert breakout["action"] == "HOLD"
    assert breakout["m3_context"]["timing_reliable"] is True

    row["floor_week_m3_confidence"] = 0.20
    out = run_strategies([row], cfg, session="OPEN_PLUS_2H")
    breakout = next(
        signal
        for signal in out["signals"]
        if signal["strategy_id"] == "breakout_protected_by_floor"
    )
    assert breakout["action"] == "BUY"
    assert breakout["m3_context"]["timing_reliable"] is False


def test_allocator_keeps_one_order_per_symbol() -> None:
    cfg = load_simple_yaml(Path("config/strategies.yaml"))
    out = run_strategies(_rows(), cfg, session="OPEN_PLUS_2H")

    symbols = [order["symbol"] for order in out["orders"]]
    assert len(symbols) == len(set(symbols))
    assert any(
        ("collision" in item["reason"].lower()) or ("ticker limit" in item["reason"].lower())
        for item in out["blocked"]
    )
