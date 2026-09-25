from __future__ import annotations

from strategies.base import StrategyDecision
from strategies.volatility_regime_switch import route_volatility_regime_decisions


def _decision(strategy: str, symbol: str, side: str, net_alpha: float) -> StrategyDecision:
    return StrategyDecision(
        strategy_id=strategy,
        symbol=symbol,
        side=side,
        score=net_alpha,
        qty=10,
        horizon="d1",
        entry_reason=f"{strategy} source",
        exit_reason="source exit",
        stop_price=95.0 if side == "BUY" else 105.0,
        take_profit_price=110.0 if side == "BUY" else 90.0,
        expected_return=net_alpha if side == "BUY" else -net_alpha,
        expected_range=15.0,
        timing_alignment=0.5,
        gross_alpha_pct=net_alpha + 0.0061,
        net_alpha_pct=net_alpha,
        cost_pct=0.0061,
        alpha_source=f"{strategy}_alpha",
        payoff_room_pct=0.10,
    )


def _cfg() -> dict:
    return {
        "low_regime_max_score": 0.8,
        "high_regime_min_score": 1.2,
        "low_source": "mean_reversion_floor_w1",
        "high_source": "breakout_protected_by_floor",
    }


def test_low_regime_routes_mean_reversion_and_high_routes_breakout() -> None:
    decisions = {
        "breakout_protected_by_floor": [
            _decision("breakout_protected_by_floor", "LOW", "BUY", 0.02),
            _decision("breakout_protected_by_floor", "HIGH", "BUY", 0.02),
        ],
        "mean_reversion_floor_w1": [
            _decision("mean_reversion_floor_w1", "LOW", "BUY", 0.015),
            _decision("mean_reversion_floor_w1", "HIGH", "BUY", 0.015),
        ],
    }
    rows = {
        "LOW": {"symbol": "LOW", "vol_regime": "LOW"},
        "HIGH": {"symbol": "HIGH", "vol_regime": "HIGH"},
    }

    output = route_volatility_regime_decisions(decisions, rows, _cfg())
    by_symbol = {item.symbol: item for item in output}

    assert by_symbol["LOW"].side == "BUY"
    assert "source=mean_reversion_floor_w1" in by_symbol["LOW"].entry_reason
    assert by_symbol["HIGH"].side == "BUY"
    assert "source=breakout_protected_by_floor" in by_symbol["HIGH"].entry_reason


def test_normal_regime_fails_closed_on_direction_conflict() -> None:
    decisions = {
        "breakout_protected_by_floor": [
            _decision("breakout_protected_by_floor", "AAA", "BUY", 0.02)
        ],
        "mean_reversion_floor_w1": [
            _decision("mean_reversion_floor_w1", "AAA", "SELL", 0.03)
        ],
    }
    rows = {"AAA": {"symbol": "AAA", "vol_regime": "NORMAL"}}

    decision = route_volatility_regime_decisions(decisions, rows, _cfg())[0]

    assert decision.side == "HOLD"
    assert "conflicting" in decision.entry_reason


def test_normal_regime_preserves_source_alpha_when_directions_agree() -> None:
    decisions = {
        "breakout_protected_by_floor": [
            _decision("breakout_protected_by_floor", "AAA", "BUY", 0.02)
        ],
        "mean_reversion_floor_w1": [
            _decision("mean_reversion_floor_w1", "AAA", "BUY", 0.03)
        ],
    }
    rows = {"AAA": {"symbol": "AAA", "vol_regime": "NORMAL"}}

    decision = route_volatility_regime_decisions(decisions, rows, _cfg())[0]

    assert decision.side == "BUY"
    assert decision.net_alpha_pct == 0.03
    assert decision.alpha_source.startswith("routed:mean_reversion_floor_w1:")


def test_regime_score_is_used_only_when_label_is_missing() -> None:
    decisions = {
        "breakout_protected_by_floor": [
            _decision("breakout_protected_by_floor", "AAA", "BUY", 0.02)
        ]
    }
    rows = {"AAA": {"symbol": "AAA", "vol_regime_score": 1.4}}

    decision = route_volatility_regime_decisions(decisions, rows, _cfg())[0]

    assert decision.side == "BUY"
    assert "regime=HIGH" in decision.entry_reason
