from __future__ import annotations

import pytest

from strategies.breakout_protected_by_floor import generate_breakout_floor_orders
from strategies.common import alpha_hurdle, round_trip_cost_bps
from strategies.weekly_opportunity_ridge import generate_weekly_opportunity_orders


def _global_cfg() -> dict:
    return {
        "portfolio": {"nav_usd": 10_000.0, "max_position_pct_nav": 0.20},
        "costs": {
            "broker_commission_bps": 2.0,
            "platform_fee_bps_per_side": 24.0,
            "slippage_bps": 3.0,
            "sell_fee_bps": 3.0,
        },
        "guards": {
            "min_range_vs_roundtrip_cost_multiple": 1.50,
            "min_net_edge_pct": 0.003,
        },
        "m3_context": {"enabled": False},
    }


def _weekly_cfg() -> dict:
    return {
        "entry": {
            "buy_top_fraction": 0.10,
            "retain_top_fraction": 0.20,
            "sell_bottom_fraction": 0.10,
            "min_buy_score": 0.0,
            "max_sell_score": 0.0,
            "min_reward_risk": 1.20,
            "min_net_alpha_pct": 0.004,
            "min_alpha_to_cost_multiple": 2.0,
        },
        "position_sizing": {
            "risk_budget_pct_nav": 0.01,
            "max_weight_pct_nav": 0.20,
            "max_notional_usd": 50_000.0,
        },
        "risk": {"stop_buffer_pct": 0.003},
        "liquidity": {"min_avg_dollar_volume": 1_000_000.0},
    }


def _row(symbol: str, score: float) -> dict:
    return {
        "symbol": symbol,
        "close": 100.0,
        "floor_q1": 95.0,
        "ceiling_q1": 120.0,
        "risk_floor_q1": 92.0,
        "risk_ceiling_q1": 123.0,
        "risk_geometry_available_q1": True,
        "weekly_opportunity_score": score,
        "avg_dollar_volume": 50_000_000.0,
    }


def test_round_trip_cost_contract_is_61_bps() -> None:
    cfg = _global_cfg()
    assert round_trip_cost_bps(cfg) == pytest.approx(61.0)
    passed, alpha = alpha_hurdle(0.0125, cfg, _weekly_cfg())
    assert passed is True
    assert alpha["cost_pct"] == pytest.approx(0.0061)
    assert alpha["net_alpha_pct"] == pytest.approx(0.0064)


def test_weekly_does_not_treat_large_ceiling_distance_as_expected_alpha() -> None:
    cfg = _global_cfg()
    strategy = _weekly_cfg()
    row = _row("AAA", 0.10)

    decisions = generate_weekly_opportunity_orders(
        [row],
        cfg,
        strategy,
        "CLOSE",
    )

    assert decisions[0].side == "HOLD"
    # Model-implied return is score * predicted downside = 0.10 * 5% = 0.5%.
    # The 20% ceiling distance must not rescue a sub-cost signal.
    assert row["ceiling_q1"] / row["close"] - 1.0 == pytest.approx(0.20)


def test_weekly_uses_model_implied_return_then_subtracts_costs() -> None:
    cfg = _global_cfg()
    strategy = _weekly_cfg()
    row = _row("AAA", 0.50)

    decision = generate_weekly_opportunity_orders(
        [row],
        cfg,
        strategy,
        "CLOSE",
    )[0]

    assert decision.side == "BUY"
    assert decision.expected_return == pytest.approx(0.025)
    assert decision.gross_alpha_pct == pytest.approx(0.025)
    assert decision.cost_pct == pytest.approx(0.0061)
    assert decision.net_alpha_pct == pytest.approx(0.0189)
    assert decision.payoff_room_pct == pytest.approx(0.20)
    assert decision.alpha_source == "weekly_ridge_score_x_predicted_q1_downside"


def test_weekly_hysteresis_retains_existing_top_20pct_name_outside_entry_top_10pct() -> None:
    cfg = _global_cfg()
    strategy = _weekly_cfg()
    rows = [_row(chr(ord("A") + idx), 1.0 - idx * 0.08) for idx in range(10)]

    decisions = generate_weekly_opportunity_orders(
        rows,
        cfg,
        strategy,
        "CLOSE",
        held_symbols={"B"},
    )
    by_symbol = {decision.symbol: decision for decision in decisions}

    assert by_symbol["A"].side == "BUY"
    assert by_symbol["B"].side == "BUY"
    assert "retained_by_hysteresis" in by_symbol["B"].entry_reason
    assert sum(decision.side == "BUY" for decision in decisions) == 2


def test_breakout_requires_directional_alpha_even_with_huge_payoff_room() -> None:
    cfg = _global_cfg()
    strategy = {
        "entry": {
            "min_abs_trend_score": 0.001,
            "min_reward_risk": 1.0,
            "min_net_alpha_pct": 0.003,
            "min_alpha_to_cost_multiple": 1.5,
            "momentum_weight": 1.0,
            "relative_strength_weight": 0.0,
        },
        "position_sizing": {
            "risk_budget_pct_nav": 0.01,
            "max_weight_pct_nav": 0.20,
            "max_notional_usd": 50_000.0,
        },
        "risk": {"stop_buffer_pct": 0.0},
        "liquidity": {"min_avg_dollar_volume": 1_000_000.0},
    }
    row = {
        "symbol": "AAA",
        "close": 100.0,
        "floor_d1": 95.0,
        "ceiling_d1": 150.0,
        "risk_floor_d1": 90.0,
        "risk_ceiling_d1": 155.0,
        "risk_geometry_available_d1": True,
        "momentum_20": 0.005,
        "rel_strength_20": 0.0,
        "avg_dollar_volume": 50_000_000.0,
        "confidence_score": 1.0,
    }

    decision = generate_breakout_floor_orders(
        [row],
        cfg,
        strategy,
        "CLOSE",
    )[0]

    assert decision.side == "HOLD"
