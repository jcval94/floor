from __future__ import annotations

from strategies.floor_ceiling_reclaim import generate_floor_ceiling_reclaim_orders
from strategies.research_registry import RESEARCH_ONLY_STRATEGY_IDS


def _global_cfg() -> dict:
    return {
        "portfolio": {
            "nav_usd": 10_000.0,
            "max_position_pct_nav": 0.08,
        },
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
            "touch_tolerance_pct": 0.002,
            "confirmation_buffer_pct": 0.001,
            "min_relative_volume": 0.80,
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


def _row() -> dict:
    return {
        "symbol": "AAA",
        "close": 100.20,
        "floor_d1": 100.0,
        "ceiling_d1": 110.0,
        "risk_floor_d1": 96.0,
        "risk_ceiling_d1": 114.0,
        "risk_geometry_available_d1": True,
        "confidence_score": 0.80,
        "avg_dollar_volume": 50_000_000.0,
        "relative_volume_20": 1.20,
        "intraday_prev_price": 99.80,
        "intraday_low": 99.50,
        "intraday_high": 101.00,
    }


def test_reclaim_is_isolated_from_active_strategy_registry() -> None:
    from strategies.registry import ACTIVE_STRATEGY_IDS

    assert "floor_ceiling_reclaim" in RESEARCH_ONLY_STRATEGY_IDS
    assert "floor_ceiling_reclaim" not in ACTIVE_STRATEGY_IDS


def test_reclaim_fails_closed_without_point_in_time_intraday_context() -> None:
    row = _row()
    row.pop("intraday_prev_price")

    decision = generate_floor_ceiling_reclaim_orders(
        [row],
        _global_cfg(),
        _strategy_cfg(),
        "OPEN_PLUS_2H",
    )[0]

    assert decision.side == "HOLD"
    assert "intraday reclaim context unavailable" in decision.entry_reason


def test_reclaim_event_without_explicit_alpha_remains_hold() -> None:
    decision = generate_floor_ceiling_reclaim_orders(
        [_row()],
        _global_cfg(),
        _strategy_cfg(),
        "OPEN_PLUS_2H",
    )[0]

    assert decision.side == "HOLD"
    assert "explicit floor_reclaim_alpha_pct is unavailable" in decision.entry_reason


def test_confirmed_floor_reclaim_with_cost_adjusted_alpha_can_buy() -> None:
    row = _row()
    row["floor_reclaim_alpha_pct"] = 0.020

    decision = generate_floor_ceiling_reclaim_orders(
        [row],
        _global_cfg(),
        _strategy_cfg(),
        "OPEN_PLUS_2H",
    )[0]

    assert decision.side == "BUY"
    assert decision.alpha_source == "floor_reclaim_alpha_pct"
    assert decision.gross_alpha_pct == 0.020
    assert decision.net_alpha_pct > 0
    assert decision.stop_price < row["floor_d1"]
    assert decision.take_profit_price == row["ceiling_d1"]


def test_confirmed_ceiling_rejection_with_alpha_can_sell() -> None:
    row = _row()
    row.update(
        {
            "close": 109.70,
            "intraday_prev_price": 110.20,
            "intraday_low": 108.80,
            "intraday_high": 110.50,
            "ceiling_rejection_alpha_pct": 0.020,
        }
    )

    decision = generate_floor_ceiling_reclaim_orders(
        [row],
        _global_cfg(),
        _strategy_cfg(),
        "OPEN_PLUS_4H",
    )[0]

    assert decision.side == "SELL"
    assert decision.alpha_source == "ceiling_rejection_alpha_pct"
    assert decision.expected_return < 0
    assert decision.stop_price > row["ceiling_d1"]
    assert decision.take_profit_price == row["floor_d1"]


def test_reclaim_score_is_invariant_to_range_coverage() -> None:
    low = _row()
    high = _row()
    low.update({"floor_reclaim_alpha_pct": 0.02, "confidence_score": 0.05})
    high.update({"floor_reclaim_alpha_pct": 0.02, "confidence_score": 0.95})

    low_decision = generate_floor_ceiling_reclaim_orders(
        [low], _global_cfg(), _strategy_cfg(), "OPEN_PLUS_2H"
    )[0]
    high_decision = generate_floor_ceiling_reclaim_orders(
        [high], _global_cfg(), _strategy_cfg(), "OPEN_PLUS_2H"
    )[0]

    assert low_decision.side == high_decision.side == "BUY"
    assert low_decision.score == high_decision.score
