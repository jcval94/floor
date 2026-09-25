from __future__ import annotations

from strategies.base import StrategyDecision
from strategies.relative_strength_rotation import build_relative_strength_rotation


def _buy(symbol: str, alpha: float = 0.02) -> StrategyDecision:
    return StrategyDecision(
        strategy_id="weekly_opportunity_ridge",
        symbol=symbol,
        side="BUY",
        score=alpha,
        qty=10,
        horizon="q1",
        entry_reason="source buy",
        exit_reason="source exit",
        stop_price=90.0,
        take_profit_price=120.0,
        expected_return=alpha + 0.0061,
        expected_range=30.0,
        timing_alignment=0.5,
        gross_alpha_pct=alpha + 0.0061,
        net_alpha_pct=alpha,
        cost_pct=0.0061,
        alpha_source="weekly_net_alpha",
        payoff_room_pct=0.20,
    )


def _cfg() -> dict:
    return {
        "source_priority": (
            "weekly_opportunity_ridge,"
            "breakout_protected_by_floor,"
            "mean_reversion_floor_w1"
        ),
        "weight_4w": 0.50,
        "weight_8w": 0.30,
        "weight_13w": 0.20,
        "min_rs_horizons": 2,
        "min_composite_rs": 0.0,
        "max_abs_beta": 1.80,
        "beta_penalty_weight": 0.50,
        "top_fraction": 0.34,
        "retain_top_fraction": 0.67,
        "max_positions": 5,
        "max_per_sector": 2,
    }


def _row(symbol: str, rs: float, sector: str = "Technology", beta: float = 1.0) -> dict:
    return {
        "symbol": symbol,
        "sector": sector,
        "close": 100.0,
        "rel_strength_4w": rs,
        "rel_strength_8w": rs,
        "rel_strength_13w": rs,
        "beta_20": beta,
    }


def test_rotation_ranks_only_cost_valid_source_buys() -> None:
    decisions = {
        "weekly_opportunity_ridge": [
            _buy("A", 0.01),
            _buy("B", 0.02),
            _buy("C", 0.03),
        ]
    }
    rows = {
        "A": _row("A", 0.03),
        "B": _row("B", 0.06),
        "C": _row("C", -0.02),
    }

    output = build_relative_strength_rotation(decisions, rows, _cfg())
    by_symbol = {item.symbol: item for item in output}

    assert by_symbol["A"].side == "BUY"
    assert by_symbol["B"].side == "BUY"
    assert by_symbol["C"].side == "HOLD"
    assert by_symbol["B"].net_alpha_pct == 0.02
    assert by_symbol["B"].alpha_source.startswith(
        "rotation_preserves:weekly_opportunity_ridge:"
    )


def test_rotation_uses_beta_as_penalty_not_expected_alpha() -> None:
    decisions = {"weekly_opportunity_ridge": [_buy("A"), _buy("B")]}
    rows = {
        "A": _row("A", 0.05, beta=1.0),
        "B": _row("B", 0.05, beta=1.7),
    }
    cfg = _cfg()
    cfg["top_fraction"] = 0.25

    output = build_relative_strength_rotation(decisions, rows, cfg)
    by_symbol = {item.symbol: item for item in output}

    assert by_symbol["A"].side == "BUY"
    assert by_symbol["B"].side == "HOLD"
    assert by_symbol["A"].net_alpha_pct == 0.02


def test_rotation_hysteresis_can_retain_second_ranked_existing_name() -> None:
    decisions = {
        "weekly_opportunity_ridge": [
            _buy("A"),
            _buy("B"),
            _buy("C"),
            _buy("D"),
        ]
    }
    rows = {
        "A": _row("A", 0.08),
        "B": _row("B", 0.07),
        "C": _row("C", 0.06),
        "D": _row("D", 0.05),
    }
    cfg = _cfg()
    cfg["top_fraction"] = 0.25
    cfg["retain_top_fraction"] = 0.50

    output = build_relative_strength_rotation(
        decisions,
        rows,
        cfg,
        held_symbols={"B"},
    )
    by_symbol = {item.symbol: item for item in output}

    assert by_symbol["A"].side == "BUY"
    assert by_symbol["B"].side == "BUY"
    assert "retained_by_hysteresis" in by_symbol["B"].entry_reason


def test_rotation_respects_sector_concentration_limit() -> None:
    decisions = {
        "weekly_opportunity_ridge": [_buy("A"), _buy("B"), _buy("C")]
    }
    rows = {
        "A": _row("A", 0.08, sector="Technology"),
        "B": _row("B", 0.07, sector="Technology"),
        "C": _row("C", 0.06, sector="Financials"),
    }
    cfg = _cfg()
    cfg["top_fraction"] = 1.0
    cfg["max_per_sector"] = 1

    output = build_relative_strength_rotation(decisions, rows, cfg)
    buys = [item.symbol for item in output if item.side == "BUY"]

    assert "A" in buys
    assert "B" not in buys
    assert "C" in buys
