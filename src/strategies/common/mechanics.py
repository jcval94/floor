from __future__ import annotations

from typing import Any

from contracts.trading import round_trip_cost_bps_from_contract
from strategies.base import StrategyDecision


def to_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def platform_fee_bps_per_side(cfg: dict) -> float:
    return to_float(cfg.get("costs", {}).get("platform_fee_bps_per_side"), 0.0)


def round_trip_cost_bps(cfg: dict) -> float:
    return round_trip_cost_bps_from_contract(dict(cfg.get("costs", {})))


def round_trip_cost_pct(cfg: dict) -> float:
    return round_trip_cost_bps(cfg) / 10000.0


def alpha_after_costs(gross_alpha_pct: float, cfg: dict) -> dict[str, float]:
    """Evaluate directional alpha after the exact round-trip friction contract.

    This function deliberately does not inspect floor/ceiling geometry. Those
    boundaries describe payoff/risk space, not expected return.
    """

    gross = max(0.0, to_float(gross_alpha_pct))
    cost = round_trip_cost_pct(cfg)
    return {
        "gross_alpha_pct": gross,
        "cost_pct": cost,
        "net_alpha_pct": gross - cost,
    }


def alpha_hurdle(
    gross_alpha_pct: float,
    cfg: dict,
    strategy_cfg: dict,
) -> tuple[bool, dict[str, float]]:
    entry = strategy_cfg.get("entry", {})
    alpha = alpha_after_costs(gross_alpha_pct, cfg)
    min_net = to_float(
        entry.get(
            "min_net_alpha_pct",
            entry.get(
                "min_net_edge_pct",
                cfg.get("guards", {}).get("min_net_edge_pct", 0.0),
            ),
        ),
        0.0,
    )
    min_multiple = max(
        0.0,
        to_float(
            entry.get(
                "min_alpha_to_cost_multiple",
                cfg.get("guards", {}).get(
                    "min_range_vs_roundtrip_cost_multiple",
                    1.0,
                ),
            ),
            1.0,
        ),
    )
    alpha["min_net_alpha_pct"] = min_net
    alpha["min_alpha_to_cost_multiple"] = min_multiple
    alpha["required_gross_alpha_pct"] = max(
        alpha["cost_pct"] * min_multiple,
        alpha["cost_pct"] + min_net,
    )
    return (
        alpha["net_alpha_pct"] >= min_net
        and alpha["gross_alpha_pct"] >= alpha["cost_pct"] * min_multiple,
        alpha,
    )


def payoff_room_clears_cost(
    payoff_room_pct: float,
    cfg: dict,
    strategy_cfg: dict | None = None,
) -> bool:
    """Require enough target room to make execution friction economically sane.

    This is a feasibility gate only. It must never be described as expected
    alpha or expected return.
    """

    strategy_cfg = strategy_cfg or {}
    entry = strategy_cfg.get("entry", {})
    multiple = max(
        0.0,
        to_float(
            entry.get(
                "min_payoff_to_cost_multiple",
                cfg.get("guards", {}).get(
                    "min_range_vs_roundtrip_cost_multiple",
                    1.5,
                ),
            ),
            1.5,
        ),
    )
    return max(0.0, to_float(payoff_room_pct)) >= round_trip_cost_pct(cfg) * multiple


def net_edge(gross_pct: float, cfg: dict) -> float:
    """Backward-compatible alias for old callers.

    New strategy code should use alpha_hurdle/alpha_after_costs and reserve
    floor/ceiling distances for payoff geometry.
    """

    return alpha_after_costs(gross_pct, cfg)["net_alpha_pct"]


def geometry(row: dict, horizon: str) -> dict[str, Any]:
    """Return central opportunity geometry plus a separately calibrated risk boundary.

    Legacy champions do not contain risk geometry.  In that case stops fall back
    to the central boundary, but the result is explicitly marked uncalibrated so
    callers and dashboards cannot mistake it for a risk quantile.
    """

    close = to_float(row.get("close"))
    floor = to_float(row.get(f"floor_{horizon}"))
    ceiling = to_float(row.get(f"ceiling_{horizon}"))
    risk_available = bool(row.get(f"risk_geometry_available_{horizon}", False))
    risk_floor = to_float(row.get(f"risk_floor_{horizon}"))
    risk_ceiling = to_float(row.get(f"risk_ceiling_{horizon}"))
    if not risk_available or risk_floor <= 0:
        risk_floor = floor
    if not risk_available or risk_ceiling <= 0:
        risk_ceiling = ceiling

    if (
        close <= 0
        or floor <= 0
        or ceiling <= floor
        or risk_floor <= 0
        or risk_ceiling <= risk_floor
    ):
        return {
            "close": close,
            "floor": floor,
            "ceiling": ceiling,
            "risk_floor": risk_floor,
            "risk_ceiling": risk_ceiling,
            "risk_geometry_available": risk_available,
            "geometry_semantics": (
                "central_plus_calibrated_risk"
                if risk_available
                else "central_only_legacy_fallback"
            ),
            "up": 0.0,
            "down": 0.0,
            "long_risk": 0.0,
            "short_risk": 0.0,
            "long_rr": 0.0,
            "short_rr": 0.0,
        }

    up = max(0.0, ceiling - close) / close
    down = max(0.0, close - floor) / close
    long_risk = max(0.0, close - risk_floor) / close
    short_risk = max(0.0, risk_ceiling - close) / close
    return {
        "close": close,
        "floor": floor,
        "ceiling": ceiling,
        "risk_floor": risk_floor,
        "risk_ceiling": risk_ceiling,
        "risk_geometry_available": risk_available,
        "geometry_semantics": (
            "central_plus_calibrated_risk"
            if risk_available
            else "central_only_legacy_fallback"
        ),
        "up": up,
        "down": down,
        "long_risk": long_risk,
        "short_risk": short_risk,
        "long_rr": up / max(long_risk, 1e-9),
        "short_rr": down / max(short_risk, 1e-9),
    }

def liquidity_ok(row: dict, strategy_cfg: dict) -> bool:
    adv = to_float(row.get("avg_dollar_volume", row.get("dollar_volume", 0.0)))
    minimum = to_float(
        strategy_cfg.get("liquidity", {}).get("min_avg_dollar_volume"),
        0.0,
    )
    return adv >= minimum


def risk_sized_qty(
    row: dict,
    strategy_cfg: dict,
    global_cfg: dict,
    stop: float,
    multiplier: float = 1.0,
) -> int:
    close = to_float(row.get("close"))
    if close <= 0 or stop <= 0:
        return 0

    portfolio = global_cfg.get("portfolio", {})
    nav = to_float(portfolio.get("nav_usd"))
    sizing = strategy_cfg.get("position_sizing", {})
    risk_pct = to_float(sizing.get("risk_budget_pct_nav"))
    max_notional = to_float(sizing.get("max_notional_usd"))
    max_weight = to_float(
        sizing.get("max_weight_pct_nav", portfolio.get("max_position_pct_nav", 1.0)),
        1.0,
    )
    risk_budget = nav * risk_pct * max(multiplier, 0.0)

    friction = close * round_trip_cost_bps(global_cfg) / 10000.0
    risk_per_share = abs(close - stop) + friction
    if risk_per_share <= 0:
        return 0

    by_risk = int(risk_budget / risk_per_share)
    by_notional = int(max_notional / close) if max_notional > 0 else by_risk
    by_weight = int((nav * max(max_weight, 0.0)) / close) if nav > 0 else 0
    return max(0, min(by_risk, by_notional, by_weight))


def apply_m3_context(
    row: dict,
    action: str,
    cfg: dict,
) -> tuple[bool, float, int, dict]:
    m3 = cfg.get("m3_context", {})
    if not m3.get("enabled", True):
        return True, 1.0, 0, {"enabled": False}

    close = to_float(row.get("close"))
    floor = to_float(row.get("floor_m3"))
    week = int(to_float(row.get("floor_week_m3")))
    confidence = to_float(row.get("floor_week_m3_confidence"))
    min_confidence = to_float(m3.get("min_timing_confidence"), 0.55)

    reliable = week > 0 and confidence >= min_confidence
    near = reliable and week <= int(m3.get("near_weeks", 2))
    imminent = reliable and week <= int(m3.get("imminent_weeks", 1))
    far = reliable and week >= int(m3.get("far_weeks", 6))
    above_floor = (
        (close - floor) / max(close, 1e-9)
        if close > 0 and floor > 0
        else 0.0
    )

    multiplier = 1.0
    priority = 0
    if action == "BUY" and near:
        multiplier = to_float(m3.get("size_multiplier_when_near_buy"), 0.65)
        priority += int(to_float(m3.get("priority_penalty_when_near_buy"), 1))
        if imminent:
            multiplier = min(
                multiplier,
                to_float(m3.get("size_multiplier_when_imminent_buy"), 0.50),
            )
    elif action == "SELL" and near:
        multiplier = to_float(m3.get("size_multiplier_when_near_sell"), 1.10)
        priority -= int(to_float(m3.get("priority_boost_when_near_sell"), 1))
    elif far:
        multiplier = to_float(m3.get("size_multiplier_when_far"), 1.05)

    context = {
        "enabled": True,
        "floor_m3": floor,
        "floor_week_m3": week,
        "floor_week_m3_confidence": confidence,
        "timing_reliable": reliable,
        "near_term_floor_week": near,
        "size_multiplier": multiplier,
        "priority_adjustment": priority,
        "above_floor_m3_pct": above_floor,
    }

    should_block_buy = (
        action == "BUY"
        and near
        and week >= int(m3.get("tactical_long_block_min_week", 1))
        and above_floor
        >= to_float(m3.get("tactical_long_block_if_above_floor_m3_pct"), 1.0)
    )
    return (not should_block_buy), multiplier, priority, context


def hold_decision(
    strategy_id: str,
    row: dict,
    horizon: str,
    reason: str,
    score: float = 0.0,
) -> StrategyDecision:
    return StrategyDecision(
        strategy_id=strategy_id,
        symbol=str(row["symbol"]),
        side="HOLD",
        score=score,
        qty=0,
        horizon=horizon,
        entry_reason=reason,
        exit_reason="No trade",
        stop_price=0.0,
        take_profit_price=0.0,
        expected_return=0.0,
        expected_range=0.0,
        timing_alignment=0.5,
    )
