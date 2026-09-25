from __future__ import annotations

from strategies.gap_to_floor_ceiling import generate_gap_to_floor_ceiling_orders


def _global_cfg() -> dict:
    return {
        "portfolio": {"nav_usd": 10_000.0, "max_position_pct_nav": 0.08},
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


def _strategy_cfg() -> dict:
    return {
        "entry": {
            "min_abs_gap_pct": 0.015,
            "anchor_tolerance_pct": 0.015,
            "min_reward_risk": 1.25,
            "min_net_alpha_pct": 0.003,
            "min_alpha_to_cost_multiple": 1.50,
        },
        "position_sizing": {
            "risk_budget_pct_nav": 0.005,
            "max_weight_pct_nav": 0.08,
            "max_notional_usd": 50_000.0,
        },
        "risk": {"stop_buffer_pct": 0.002},
        "liquidity": {"min_avg_dollar_volume": 9_000_000.0},
    }


def _floor_row() -> dict:
    return {
        "symbol": "AAA",
        "open": 100.0,
        "close": 150.0,
        "gap_open_to_prev_close": -0.03,
        "floor_d1": 99.5,
        "ceiling_d1": 110.0,
        "risk_floor_d1": 96.0,
        "risk_ceiling_d1": 114.0,
        "risk_geometry_available_d1": True,
        "confidence_score": 0.80,
        "avg_dollar_volume": 50_000_000.0,
    }


def test_gap_strategy_is_open_only() -> None:
    row = _floor_row()
    row["gap_floor_alpha_pct"] = 0.02

    decision = generate_gap_to_floor_ceiling_orders(
        [row], _global_cfg(), _strategy_cfg(), "OPEN_PLUS_2H"
    )[0]

    assert decision.side == "HOLD"
    assert "only evaluates at OPEN" in decision.entry_reason


def test_gap_event_without_explicit_alpha_stays_hold() -> None:
    decision = generate_gap_to_floor_ceiling_orders(
        [_floor_row()], _global_cfg(), _strategy_cfg(), "OPEN"
    )[0]

    assert decision.side == "HOLD"
    assert "gap_floor_alpha_pct is unavailable" in decision.entry_reason


def test_floor_gap_uses_open_not_close_as_entry_reference() -> None:
    row = _floor_row()
    row["gap_floor_alpha_pct"] = 0.02

    decision = generate_gap_to_floor_ceiling_orders(
        [row], _global_cfg(), _strategy_cfg(), "OPEN"
    )[0]

    assert decision.side == "BUY"
    assert decision.take_profit_price == 110.0
    assert decision.stop_price < 99.5
    assert decision.qty > 0
    assert decision.payoff_room_pct == 0.10
    assert "OPEN gap=-3.00%" in decision.entry_reason


def test_gap_through_calibrated_risk_boundary_fails_closed() -> None:
    row = _floor_row()
    row.update({"open": 95.0, "gap_floor_alpha_pct": 0.03})

    decision = generate_gap_to_floor_ceiling_orders(
        [row], _global_cfg(), _strategy_cfg(), "OPEN"
    )[0]

    assert decision.side == "HOLD"
    assert "breached calibrated risk boundary" in decision.entry_reason


def test_ceiling_gap_can_emit_cost_valid_sell() -> None:
    row = _floor_row()
    row.update(
        {
            "open": 110.0,
            "close": 80.0,
            "gap_open_to_prev_close": 0.03,
            "floor_d1": 100.0,
            "ceiling_d1": 110.5,
            "risk_floor_d1": 96.0,
            "risk_ceiling_d1": 114.0,
            "gap_ceiling_alpha_pct": 0.02,
        }
    )

    decision = generate_gap_to_floor_ceiling_orders(
        [row], _global_cfg(), _strategy_cfg(), "OPEN"
    )[0]

    assert decision.side == "SELL"
    assert decision.expected_return < 0
    assert decision.take_profit_price == 100.0
    assert decision.stop_price > 110.5


def test_gap_score_is_invariant_to_range_coverage() -> None:
    low = _floor_row()
    high = _floor_row()
    low.update({"gap_floor_alpha_pct": 0.02, "confidence_score": 0.05})
    high.update({"gap_floor_alpha_pct": 0.02, "confidence_score": 0.95})

    low_decision = generate_gap_to_floor_ceiling_orders(
        [low], _global_cfg(), _strategy_cfg(), "OPEN"
    )[0]
    high_decision = generate_gap_to_floor_ceiling_orders(
        [high], _global_cfg(), _strategy_cfg(), "OPEN"
    )[0]

    assert low_decision.side == high_decision.side == "BUY"
    assert low_decision.score == high_decision.score
