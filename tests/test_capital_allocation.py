from __future__ import annotations

import pytest

from league.capital_challenger import build_capital_challenger_targets
from portfolio.risk_allocator import PortfolioState, cap_order_quantity
from strategies.base import StrategyDecision


def _global_cfg() -> dict:
    return {
        "portfolio": {
            "nav_usd": 10000.0,
            "max_position_pct_nav": 0.20,
            "max_gross_exposure_pct_nav": 0.90,
            "max_portfolio_heat_pct_nav": 0.04,
            "max_sector_exposure_pct_nav": 0.35,
            "min_cash_pct_nav": 0.10,
        },
        "costs": {
            "broker_commission_bps": 2.0,
            "slippage_bps": 3.0,
            "platform_fee_bps_per_side": 24.0,
        },
    }


def _strategy_cfg() -> dict:
    return {
        "position_sizing": {
            "risk_budget_pct_nav": 0.0075,
            "max_weight_pct_nav": 0.20,
            "max_notional_usd": 50000.0,
        }
    }


def _decision(
    strategy_id: str,
    symbol: str,
    score: float,
    stop: float,
    take: float = 115.0,
) -> StrategyDecision:
    return StrategyDecision(
        strategy_id=strategy_id,
        symbol=symbol,
        side="BUY",
        score=score,
        qty=100,
        horizon="w1",
        entry_reason="test",
        exit_reason="test",
        stop_price=stop,
        take_profit_price=take,
        expected_return=0.05,
        expected_range=20.0,
        timing_alignment=0.8,
    )


def test_position_cap_never_allows_more_than_20pct_nav() -> None:
    state = PortfolioState(equity=10000.0, cash=10000.0)
    result = cap_order_quantity(
        proposed_qty=100,
        side="BUY",
        entry_price=100.0,
        stop_price=95.0,
        sector="Technology",
        strategy_cfg=_strategy_cfg(),
        global_cfg=_global_cfg(),
        state=state,
    )

    assert result.quantity == 20
    assert result.notional_usd == pytest.approx(2000.0)
    assert result.weight_pct_nav <= 0.20
    assert result.binding_limit == "max_position"


def test_portfolio_heat_cap_blocks_risk_beyond_4pct() -> None:
    state = PortfolioState(
        equity=10000.0,
        cash=10000.0,
        gross_exposure=5000.0,
        open_risk=390.0,
    )
    result = cap_order_quantity(
        proposed_qty=100,
        side="BUY",
        entry_price=100.0,
        stop_price=95.0,
        sector="Technology",
        strategy_cfg=_strategy_cfg(),
        global_cfg=_global_cfg(),
        state=state,
    )

    assert result.quantity <= 1
    assert result.portfolio_heat_after_pct_nav <= 0.04 + 1e-12
    assert result.binding_limit == "max_portfolio_heat"


def test_cash_reserve_keeps_10pct_nav_unspent() -> None:
    state = PortfolioState(equity=10000.0, cash=1500.0)
    result = cap_order_quantity(
        proposed_qty=100,
        side="BUY",
        entry_price=100.0,
        stop_price=95.0,
        sector="Technology",
        strategy_cfg=_strategy_cfg(),
        global_cfg=_global_cfg(),
        state=state,
    )

    assert result.quantity == 5
    assert result.notional_usd == pytest.approx(500.0)
    assert result.binding_limit == "cash_reserve"


def test_challenger_respects_gross_heat_sector_and_position_caps() -> None:
    cfg = _global_cfg()
    rows = {
        "AAA": {"symbol": "AAA", "close": 100.0, "sector": "Technology"},
        "BBB": {"symbol": "BBB", "close": 100.0, "sector": "Technology"},
        "CCC": {"symbol": "CCC", "close": 100.0, "sector": "Financials"},
        "DDD": {"symbol": "DDD", "close": 100.0, "sector": "Healthcare"},
    }
    decisions = {
        "weekly_opportunity_ridge": [
            _decision("weekly_opportunity_ridge", "AAA", 3.0, 95.0),
            _decision("weekly_opportunity_ridge", "BBB", 2.0, 95.0),
            _decision("weekly_opportunity_ridge", "CCC", 1.0, 95.0),
        ],
        "breakout_protected_by_floor": [
            _decision("breakout_protected_by_floor", "AAA", 0.9, 96.0),
            _decision("breakout_protected_by_floor", "DDD", 0.8, 94.0),
        ],
        "mean_reversion_floor_w1": [
            _decision("mean_reversion_floor_w1", "BBB", 0.7, 93.0),
        ],
        "cross_horizon_asymmetry": [
            _decision("cross_horizon_asymmetry", "CCC", 0.6, 92.0),
        ],
    }
    challenger_cfg = {
        "risk_budget_pct_nav": 0.0075,
        "min_risk_scale": 0.65,
        "max_position_pct_nav": 0.20,
        "max_gross_exposure_pct_nav": 0.90,
        "max_portfolio_heat_pct_nav": 0.04,
        "max_sector_exposure_pct_nav": 0.35,
        "min_position_weight": 0.02,
        "quality_floor": 0.45,
        "consensus_bonus": 0.15,
        "source_weights": {
            "weekly_opportunity_ridge": 1.0,
            "breakout_protected_by_floor": 1.0,
            "mean_reversion_floor_w1": 0.90,
            "cross_horizon_asymmetry": 0.95,
        },
    }

    targets = build_capital_challenger_targets(
        decisions,
        rows,
        cfg,
        challenger_cfg,
    )

    assert targets
    assert sum(spec["weight"] for spec in targets.values()) <= 0.90 + 1e-12
    assert all(spec["weight"] <= 0.20 + 1e-12 for spec in targets.values())
    assert sum(
        spec["weight"] * spec["stop_risk_pct"] for spec in targets.values()
    ) <= 0.04 + 1e-12

    tech_weight = sum(
        spec["weight"]
        for symbol, spec in targets.items()
        if rows[symbol]["sector"] == "Technology"
    )
    assert tech_weight <= 0.35 + 1e-12
    assert "AAA" in targets
    assert targets["AAA"]["consensus_count"] == 2
    assert set(targets["AAA"]["source_strategies"]) == {
        "weekly_opportunity_ridge",
        "breakout_protected_by_floor",
    }
