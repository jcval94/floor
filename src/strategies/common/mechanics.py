from __future__ import annotations

from strategies.base import StrategyDecision


def to_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def platform_fee_bps_per_side(cfg: dict) -> float:
    return to_float(cfg.get("costs", {}).get("platform_fee_bps_per_side"), 0.0)


def round_trip_cost_bps(cfg: dict) -> float:
    costs = cfg.get("costs", {})
    broker = to_float(
        costs.get("broker_commission_bps"),
        to_float(costs.get("commission_bps"), 0.0),
    )
    slippage = to_float(costs.get("slippage_bps"), 0.0)
    return 2.0 * (broker + slippage + platform_fee_bps_per_side(cfg))


def net_edge(gross_pct: float, cfg: dict) -> float:
    return gross_pct - round_trip_cost_bps(cfg) / 10000.0


def geometry(row: dict, horizon: str) -> dict[str, float]:
    close = to_float(row.get("close"))
    floor = to_float(row.get(f"floor_{horizon}"))
    ceiling = to_float(row.get(f"ceiling_{horizon}"))
    if close <= 0 or floor <= 0 or ceiling <= floor:
        return {
            "close": close,
            "floor": floor,
            "ceiling": ceiling,
            "up": 0.0,
            "down": 0.0,
            "long_rr": 0.0,
            "short_rr": 0.0,
        }
    up = max(0.0, ceiling - close) / close
    down = max(0.0, close - floor) / close
    return {
        "close": close,
        "floor": floor,
        "ceiling": ceiling,
        "up": up,
        "down": down,
        "long_rr": up / max(down, 1e-9),
        "short_rr": down / max(up, 1e-9),
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
