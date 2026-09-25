from __future__ import annotations

from collections import Counter
from datetime import date

from replay.research_challenger_tournament import (
    _evidence_readiness,
    _research_targets,
    _runtime_config,
)
from strategies.base import StrategyDecision


def _decision(strategy: str, symbol: str, alpha: float = 0.02) -> StrategyDecision:
    return StrategyDecision(
        strategy_id=strategy,
        symbol=symbol,
        side="BUY",
        score=alpha,
        qty=10,
        horizon="d1",
        entry_reason="source",
        exit_reason="source exit",
        stop_price=90.0,
        take_profit_price=120.0,
        expected_return=alpha + 0.0061,
        expected_range=30.0,
        timing_alignment=0.5,
        gross_alpha_pct=alpha + 0.0061,
        net_alpha_pct=alpha,
        cost_pct=0.0061,
        alpha_source="source_alpha",
        payoff_room_pct=0.20,
    )


def _strategies_cfg() -> dict:
    return {
        "portfolio": {"nav_usd": 10_000.0},
        "costs": {
            "commission_bps": 26.0,
            "broker_commission_bps": 2.0,
            "platform_fee_bps_per_side": 24.0,
            "slippage_bps": 3.0,
            "sell_fee_bps": 3.0,
        },
        "strategies": {
            "weekly_opportunity_ridge": {
                "exits": {"temporal_exit_business_days": 10}
            },
            "mean_reversion_floor_w1": {
                "exits": {"temporal_exit_business_days": 5}
            },
            "cross_horizon_asymmetry": {
                "exits": {"temporal_exit_business_days": 10}
            },
        },
    }


def test_runtime_config_adds_research_members_without_promoting_them() -> None:
    league_cfg = {
        "league_id": "strategy_league_v9",
        "initial_nav_usd": 10_000,
        "members": [
            {"id": "benchmark_spy", "type": "benchmark", "required": True}
        ],
        "capital_allocation_challenger": {"max_holding_sessions": 10},
    }

    runtime = _runtime_config(
        league_cfg,
        _strategies_cfg(),
        [date(2026, 6, 22), date(2026, 9, 23)],
        {"max_holding_sessions": 5},
        {"max_holding_sessions": 10},
    )

    assert runtime["league_id"].startswith("research_challenger_closeout_")
    assert runtime["league_id"] != league_cfg["league_id"]
    by_id = {item["id"]: item for item in runtime["members"]}
    for member_id in (
        "volatility_regime_switch",
        "relative_strength_rotation",
    ):
        assert by_id[member_id]["evidence_role"] == "diagnostic_only"
        assert by_id[member_id]["promotion_eligible"] is False
        assert (
            by_id[member_id]["league_evidence_can_promote_canonical_variant"]
            is False
        )


def test_evidence_readiness_refuses_to_fake_gap_or_reclaim_alpha() -> None:
    leaderboard = {
        "rows": [
            {"strategy": "volatility_regime_switch"},
            {"strategy": "relative_strength_rotation"},
        ]
    }
    readiness = _evidence_readiness(
        leaderboard,
        {
            "volatility_regime_switch": Counter({"BUY": 4, "HOLD": 6}),
            "relative_strength_rotation": Counter({"BUY": 2, "HOLD": 8}),
        },
        {
            "volatility_regime_switch": 10,
            "relative_strength_rotation": 9,
        },
        10,
    )

    assert (
        readiness["volatility_regime_switch"]["status"]
        == "EVALUATED_RETROSPECTIVE_PIT"
    )
    assert (
        readiness["relative_strength_rotation"]["status"]
        == "EVALUATED_RETROSPECTIVE_PIT"
    )
    assert readiness["floor_ceiling_reclaim"]["status"] == "INSUFFICIENT_EVIDENCE"
    assert readiness["gap_to_floor_ceiling"]["status"] == "INSUFFICIENT_EVIDENCE"
    assert "directional alpha" in readiness["floor_ceiling_reclaim"]["reason"]
    assert "explicit directional" in readiness["gap_to_floor_ceiling"]["reason"]
    assert all(item["promotion_eligible"] is False for item in readiness.values())


def test_research_targets_route_existing_cost_valid_sources(monkeypatch) -> None:
    scored = [
        {
            "symbol": "AAA",
            "close": 100.0,
            "sector": "Technology",
            "vol_regime": "HIGH",
            "vol_regime_score": 1.4,
            "rel_strength_4w": 0.08,
            "rel_strength_8w": 0.07,
            "rel_strength_13w": 0.06,
            "beta_20": 1.0,
        },
        {
            "symbol": "BBB",
            "close": 100.0,
            "sector": "Financials",
            "vol_regime": "LOW",
            "vol_regime_score": 0.7,
            "rel_strength_4w": -0.01,
            "rel_strength_8w": -0.02,
            "rel_strength_13w": -0.03,
            "beta_20": 1.0,
        },
    ]
    sources = {
        "weekly_opportunity_ridge": [_decision("weekly_opportunity_ridge", "AAA")],
        "breakout_protected_by_floor": [
            _decision("breakout_protected_by_floor", "AAA")
        ],
        "mean_reversion_floor_w1": [
            _decision("mean_reversion_floor_w1", "BBB")
        ],
    }

    monkeypatch.setattr(
        "replay.research_challenger_tournament._source_decisions",
        lambda *args, **kwargs: (scored, sources),
    )

    targets, counts, coverage = _research_targets(
        rows=scored,
        strategies_cfg=_strategies_cfg(),
        weekly_artifact={},
        volatility_cfg={
            "low_source": "mean_reversion_floor_w1",
            "high_source": "breakout_protected_by_floor",
        },
        rotation_cfg={
            "source_priority": (
                "weekly_opportunity_ridge,"
                "breakout_protected_by_floor,"
                "mean_reversion_floor_w1"
            ),
            "weight_4w": 0.5,
            "weight_8w": 0.3,
            "weight_13w": 0.2,
            "min_rs_horizons": 2,
            "min_composite_rs": 0.0,
            "top_fraction": 1.0,
            "retain_top_fraction": 1.0,
            "max_positions": 5,
            "max_per_sector": 2,
        },
        current_positions={},
        include_volatility=True,
        include_rotation=True,
    )

    assert set(targets["volatility_regime_switch"]) == {"AAA", "BBB"}
    assert set(targets["relative_strength_rotation"]) == {"AAA"}
    assert counts["volatility_regime_switch"]["BUY"] == 2
    assert counts["relative_strength_rotation"]["BUY"] == 1
    assert coverage["volatility_regime_switch"] == 2
    assert coverage["relative_strength_rotation"] == 2
