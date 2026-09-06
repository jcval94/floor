from __future__ import annotations

import math

from strategies.base import StrategyDecision


def _f(value: object, default: float = 0.0) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def platform_fee_bps_per_side(cfg: dict) -> float:
    return _f(cfg.get("costs", {}).get("platform_fee_bps_per_side"), 0.0)


def round_trip_cost_bps(cfg: dict) -> float:
    costs = cfg.get("costs", {})
    broker = _f(
        costs.get("broker_commission_bps"),
        _f(costs.get("commission_bps"), 0.0),
    )
    slippage = _f(costs.get("slippage_bps"), 0.0)
    return 2.0 * (broker + slippage + platform_fee_bps_per_side(cfg))


def _net(gross_pct: float, cfg: dict) -> float:
    return gross_pct - round_trip_cost_bps(cfg) / 10000.0


def _geometry(row: dict, horizon: str) -> dict[str, float]:
    close = _f(row.get("close"))
    floor = _f(row.get(f"floor_{horizon}"))
    ceiling = _f(row.get(f"ceiling_{horizon}"))
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


def _liquid(row: dict, strategy_cfg: dict) -> bool:
    adv = _f(row.get("avg_dollar_volume", row.get("dollar_volume", 0.0)))
    minimum = _f(
        strategy_cfg.get("liquidity", {}).get("min_avg_dollar_volume"),
        0.0,
    )
    return adv >= minimum


def _size(
    row: dict,
    strategy_cfg: dict,
    global_cfg: dict,
    stop: float,
    multiplier: float = 1.0,
) -> int:
    close = _f(row.get("close"))
    if close <= 0 or stop <= 0:
        return 0

    nav = _f(global_cfg.get("portfolio", {}).get("nav_usd"))
    sizing = strategy_cfg.get("position_sizing", {})
    risk_pct = _f(sizing.get("risk_budget_pct_nav"))
    max_notional = _f(sizing.get("max_notional_usd"))
    risk_budget = nav * risk_pct * max(multiplier, 0.0)

    friction = close * round_trip_cost_bps(global_cfg) / 10000.0
    risk_per_share = abs(close - stop) + friction
    if risk_per_share <= 0:
        return 0

    by_risk = int(risk_budget / risk_per_share)
    by_notional = int(max_notional / close) if max_notional > 0 else by_risk
    return max(0, min(by_risk, by_notional))


def _m3(
    row: dict,
    action: str,
    cfg: dict,
) -> tuple[bool, float, int, dict]:
    m3 = cfg.get("m3_context", {})
    if not m3.get("enabled", True):
        return True, 1.0, 0, {"enabled": False}

    close = _f(row.get("close"))
    floor = _f(row.get("floor_m3"))
    week = int(_f(row.get("floor_week_m3")))
    confidence = _f(row.get("floor_week_m3_confidence"))
    min_confidence = _f(m3.get("min_timing_confidence"), 0.55)

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
        multiplier = _f(m3.get("size_multiplier_when_near_buy"), 0.65)
        priority += int(_f(m3.get("priority_penalty_when_near_buy"), 1))
        if imminent:
            multiplier = min(
                multiplier,
                _f(m3.get("size_multiplier_when_imminent_buy"), 0.50),
            )
    elif action == "SELL" and near:
        multiplier = _f(m3.get("size_multiplier_when_near_sell"), 1.10)
        priority -= int(_f(m3.get("priority_boost_when_near_sell"), 1))
    elif far:
        multiplier = _f(m3.get("size_multiplier_when_far"), 1.05)

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
        >= _f(m3.get("tactical_long_block_if_above_floor_m3_pct"), 1.0)
    )
    return (not should_block_buy), multiplier, priority, context


def _hold(
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


def generate_breakout_floor_orders(
    rows: list[dict],
    global_cfg: dict,
    strategy_cfg: dict,
    session: str,
) -> list[StrategyDecision]:
    del session

    entry = strategy_cfg.get("entry", {})
    min_trend = _f(entry.get("min_abs_trend_score"), 0.01)
    min_rr = _f(entry.get("min_reward_risk"), 1.25)
    min_net = _f(entry.get("min_net_edge_pct"), 0.003)
    momentum_weight = _f(entry.get("momentum_weight"), 0.65)
    relative_strength_weight = _f(
        entry.get("relative_strength_weight"),
        0.35,
    )
    buffer = _f(
        strategy_cfg.get("risk", {}).get("stop_buffer_pct"),
        0.0,
    )

    output: list[StrategyDecision] = []
    for row in rows:
        geometry = _geometry(row, "d1")
        trend = (
            momentum_weight * _f(row.get("momentum_20"))
            + relative_strength_weight * _f(row.get("rel_strength_20"))
        )

        action = "HOLD"
        gross_edge = 0.0
        reward_risk = 0.0
        if (
            trend >= min_trend
            and geometry["long_rr"] >= min_rr
            and _net(geometry["up"], global_cfg) >= min_net
        ):
            action = "BUY"
            gross_edge = geometry["up"]
            reward_risk = geometry["long_rr"]
        elif (
            trend <= -min_trend
            and geometry["short_rr"] >= min_rr
            and _net(geometry["down"], global_cfg) >= min_net
        ):
            action = "SELL"
            gross_edge = geometry["down"]
            reward_risk = geometry["short_rr"]

        if action == "HOLD" or not _liquid(row, strategy_cfg):
            output.append(
                _hold(
                    "breakout_protected_by_floor",
                    row,
                    "d1",
                    (
                        "HOLD: trend/range does not clear cost-adjusted gate "
                        f"(trend={trend:.4f})"
                    ),
                )
            )
            continue

        m3_ok, size_multiplier, priority, m3_context = _m3(
            row,
            action,
            global_cfg,
        )
        if not m3_ok:
            hold = _hold(
                "breakout_protected_by_floor",
                row,
                "d1",
                "HOLD: reliable M3 floor timing blocks tactical BUY",
            )
            hold.m3_context = m3_context
            output.append(hold)
            continue

        if action == "BUY":
            stop = geometry["floor"] * (1 - buffer)
            take_profit = geometry["ceiling"]
        else:
            stop = geometry["ceiling"] * (1 + buffer)
            take_profit = geometry["floor"]

        qty = _size(
            row,
            strategy_cfg,
            global_cfg,
            stop,
            size_multiplier,
        )
        if qty <= 0:
            output.append(
                _hold(
                    "breakout_protected_by_floor",
                    row,
                    "d1",
                    "HOLD: zero risk-sized quantity",
                )
            )
            continue

        confidence = max(
            0.0,
            min(1.0, _f(row.get("confidence_score"), 0.5)),
        )
        score = (
            max(0.0, _net(gross_edge, global_cfg))
            * min(reward_risk, 3.0)
            * confidence
        )
        output.append(
            StrategyDecision(
                strategy_id="breakout_protected_by_floor",
                symbol=str(row["symbol"]),
                side=action,
                score=score,
                qty=qty,
                horizon="d1",
                entry_reason=(
                    f"{action}: trend={trend:.4f}, rr={reward_risk:.2f}, "
                    f"net_edge={_net(gross_edge, global_cfg):.2%} after "
                    f"{round_trip_cost_bps(global_cfg):.0f} bps round-trip"
                ),
                exit_reason="D1 floor/ceiling or one-session timeout",
                stop_price=stop,
                take_profit_price=take_profit,
                expected_return=0.0,
                expected_range=max(
                    0.0,
                    geometry["ceiling"] - geometry["floor"],
                ),
                timing_alignment=0.5,
                m3_context=m3_context,
                priority_adjustment=priority,
            )
        )

    return output


def generate_mean_reversion_orders(
    rows: list[dict],
    global_cfg: dict,
    strategy_cfg: dict,
    session: str,
) -> list[StrategyDecision]:
    del session

    entry = strategy_cfg.get("entry", {})
    near_anchor = _f(entry.get("near_anchor_pct"), 0.02)
    min_rr = _f(entry.get("min_reward_risk"), 1.30)
    min_net = _f(entry.get("min_net_edge_pct"), 0.004)
    min_recovery = _f(entry.get("min_recovery_momentum"), -0.005)
    max_fading = _f(entry.get("max_fading_momentum"), 0.005)
    buffer = _f(
        strategy_cfg.get("risk", {}).get("stop_buffer_pct"),
        0.0,
    )

    output: list[StrategyDecision] = []
    for row in rows:
        geometry = _geometry(row, "w1")
        close = geometry["close"]
        momentum = _f(row.get("momentum_20"))

        if close <= 0 or not _liquid(row, strategy_cfg):
            output.append(
                _hold(
                    "mean_reversion_floor_w1",
                    row,
                    "w1",
                    "HOLD: invalid geometry/liquidity",
                )
            )
            continue

        floor_distance = max(0.0, close - geometry["floor"]) / close
        ceiling_distance = max(0.0, geometry["ceiling"] - close) / close
        candidates: list[tuple[str, float, float, float]] = []

        if (
            floor_distance <= near_anchor
            and momentum >= min_recovery
            and geometry["long_rr"] >= min_rr
            and _net(geometry["up"], global_cfg) >= min_net
        ):
            candidates.append(
                (
                    "BUY",
                    _net(geometry["up"], global_cfg)
                    * min(geometry["long_rr"], 3.0),
                    geometry["up"],
                    geometry["long_rr"],
                )
            )

        if (
            ceiling_distance <= near_anchor
            and momentum <= max_fading
            and geometry["short_rr"] >= min_rr
            and _net(geometry["down"], global_cfg) >= min_net
        ):
            candidates.append(
                (
                    "SELL",
                    _net(geometry["down"], global_cfg)
                    * min(geometry["short_rr"], 3.0),
                    geometry["down"],
                    geometry["short_rr"],
                )
            )

        if not candidates:
            output.append(
                _hold(
                    "mean_reversion_floor_w1",
                    row,
                    "w1",
                    (
                        "HOLD: no W1 anchor reversal "
                        f"(floor_dist={floor_distance:.2%}, "
                        f"ceiling_dist={ceiling_distance:.2%})"
                    ),
                )
            )
            continue

        action, score, gross_edge, reward_risk = max(
            candidates,
            key=lambda item: item[1],
        )
        m3_ok, size_multiplier, priority, m3_context = _m3(
            row,
            action,
            global_cfg,
        )
        if not m3_ok:
            hold = _hold(
                "mean_reversion_floor_w1",
                row,
                "w1",
                "HOLD: reliable M3 floor timing blocks tactical BUY",
            )
            hold.m3_context = m3_context
            output.append(hold)
            continue

        if action == "BUY":
            stop = geometry["floor"] * (1 - buffer)
            take_profit = geometry["ceiling"]
        else:
            stop = geometry["ceiling"] * (1 + buffer)
            take_profit = geometry["floor"]

        qty = _size(
            row,
            strategy_cfg,
            global_cfg,
            stop,
            size_multiplier,
        )
        if qty <= 0:
            output.append(
                _hold(
                    "mean_reversion_floor_w1",
                    row,
                    "w1",
                    "HOLD: zero risk-sized quantity",
                )
            )
            continue

        output.append(
            StrategyDecision(
                strategy_id="mean_reversion_floor_w1",
                symbol=str(row["symbol"]),
                side=action,
                score=score,
                qty=qty,
                horizon="w1",
                entry_reason=(
                    f"{action}: W1 anchor reversal, rr={reward_risk:.2f}, "
                    f"net_edge={_net(gross_edge, global_cfg):.2%}"
                ),
                exit_reason="Opposite W1 anchor or five-session timeout",
                stop_price=stop,
                take_profit_price=take_profit,
                expected_return=0.0,
                expected_range=max(
                    0.0,
                    geometry["ceiling"] - geometry["floor"],
                ),
                timing_alignment=0.5,
                m3_context=m3_context,
                priority_adjustment=priority,
            )
        )

    return output


def generate_cross_horizon_orders(
    rows: list[dict],
    global_cfg: dict,
    strategy_cfg: dict,
    session: str,
) -> list[StrategyDecision]:
    del session

    entry = strategy_cfg.get("entry", {})
    min_ratio = _f(entry.get("min_asymmetry_ratio"), 1.35)
    min_trend = _f(entry.get("min_abs_trend_score"), 0.005)
    min_net = _f(entry.get("min_net_edge_pct"), 0.004)

    weights = {
        "d1": _f(entry.get("d1_weight"), 0.2),
        "w1": _f(entry.get("w1_weight"), 0.3),
        "q1": _f(entry.get("q1_weight"), 0.5),
    }
    weight_total = sum(weights.values()) or 1.0
    weights = {
        horizon: value / weight_total
        for horizon, value in weights.items()
    }

    momentum_weight = _f(entry.get("momentum_weight"), 0.6)
    relative_strength_weight = _f(
        entry.get("relative_strength_weight"),
        0.4,
    )
    buffer = _f(
        strategy_cfg.get("risk", {}).get("stop_buffer_pct"),
        0.0,
    )

    output: list[StrategyDecision] = []
    for row in rows:
        geometries = {
            horizon: _geometry(row, horizon)
            for horizon in weights
        }
        incomplete = any(
            geometry["close"] <= 0
            or geometry["floor"] <= 0
            or geometry["ceiling"] <= 0
            for geometry in geometries.values()
        )
        if incomplete or not _liquid(row, strategy_cfg):
            output.append(
                _hold(
                    "cross_horizon_asymmetry",
                    row,
                    "q1",
                    "HOLD: incomplete cross-horizon geometry/liquidity",
                )
            )
            continue

        weighted_up = sum(
            weights[horizon] * geometries[horizon]["up"]
            for horizon in weights
        )
        weighted_down = sum(
            weights[horizon] * geometries[horizon]["down"]
            for horizon in weights
        )
        long_ratio = weighted_up / max(weighted_down, 1e-9)
        short_ratio = weighted_down / max(weighted_up, 1e-9)
        trend = (
            momentum_weight * _f(row.get("momentum_20"))
            + relative_strength_weight * _f(row.get("rel_strength_20"))
        )

        action = "HOLD"
        gross_edge = 0.0
        reward_risk = 0.0
        if (
            long_ratio >= min_ratio
            and trend >= min_trend
            and _net(weighted_up, global_cfg) >= min_net
        ):
            action = "BUY"
            gross_edge = weighted_up
            reward_risk = long_ratio
        elif (
            short_ratio >= min_ratio
            and trend <= -min_trend
            and _net(weighted_down, global_cfg) >= min_net
        ):
            action = "SELL"
            gross_edge = weighted_down
            reward_risk = short_ratio

        if action == "HOLD":
            output.append(
                _hold(
                    "cross_horizon_asymmetry",
                    row,
                    "q1",
                    (
                        "HOLD: asymmetry/trend inconclusive "
                        f"(long={long_ratio:.2f}, short={short_ratio:.2f}, "
                        f"trend={trend:.4f})"
                    ),
                )
            )
            continue

        m3_ok, size_multiplier, priority, m3_context = _m3(
            row,
            action,
            global_cfg,
        )
        if not m3_ok:
            hold = _hold(
                "cross_horizon_asymmetry",
                row,
                "q1",
                "HOLD: reliable M3 floor timing blocks tactical BUY",
            )
            hold.m3_context = m3_context
            output.append(hold)
            continue

        q1 = geometries["q1"]
        if action == "BUY":
            stop = q1["floor"] * (1 - buffer)
            take_profit = q1["ceiling"]
        else:
            stop = q1["ceiling"] * (1 + buffer)
            take_profit = q1["floor"]

        qty = _size(
            row,
            strategy_cfg,
            global_cfg,
            stop,
            size_multiplier,
        )
        if qty <= 0:
            output.append(
                _hold(
                    "cross_horizon_asymmetry",
                    row,
                    "q1",
                    "HOLD: zero risk-sized quantity",
                )
            )
            continue

        confidence = max(
            0.0,
            min(1.0, _f(row.get("confidence_score"), 0.5)),
        )
        score = (
            max(0.0, _net(gross_edge, global_cfg))
            * min(reward_risk, 3.0)
            * confidence
        )
        output.append(
            StrategyDecision(
                strategy_id="cross_horizon_asymmetry",
                symbol=str(row["symbol"]),
                side=action,
                score=score,
                qty=qty,
                horizon="q1",
                entry_reason=(
                    f"{action}: cross-horizon asymmetry={reward_risk:.2f}, "
                    f"trend={trend:.4f}, "
                    f"net_edge={_net(gross_edge, global_cfg):.2%}"
                ),
                exit_reason="Q1 anchor or ten-session timeout",
                stop_price=stop,
                take_profit_price=take_profit,
                expected_return=0.0,
                expected_range=max(
                    0.0,
                    q1["ceiling"] - q1["floor"],
                ),
                timing_alignment=0.5,
                m3_context=m3_context,
                priority_adjustment=priority,
            )
        )

    return output


def generate_weekly_opportunity_orders(
    rows: list[dict],
    global_cfg: dict,
    strategy_cfg: dict,
    session: str,
) -> list[StrategyDecision]:
    del session

    score_field = str(
        strategy_cfg.get("model_score_field")
        or "weekly_opportunity_score"
    )
    entry = strategy_cfg.get("entry", {})
    buy_fraction = max(
        0.0,
        min(1.0, _f(entry.get("buy_top_fraction"), 0.2)),
    )
    sell_fraction = max(
        0.0,
        min(1.0, _f(entry.get("sell_bottom_fraction"), 0.2)),
    )
    min_buy_score = _f(entry.get("min_buy_score"), 0.0)
    max_sell_score = _f(entry.get("max_sell_score"), 0.0)
    min_rr = _f(entry.get("min_reward_risk"), 1.2)
    min_net = _f(entry.get("min_net_edge_pct"), 0.004)
    buffer = _f(
        strategy_cfg.get("risk", {}).get("stop_buffer_pct"),
        0.0,
    )

    scored: list[tuple[dict, float]] = []
    for row in rows:
        raw_score = row.get(score_field)
        if raw_score in (None, ""):
            continue
        score = _f(raw_score, float("nan"))
        if math.isfinite(score):
            scored.append((row, score))

    if not scored:
        return []

    descending = sorted(
        scored,
        key=lambda item: item[1],
        reverse=True,
    )
    buy_count = (
        max(1, math.ceil(len(descending) * buy_fraction))
        if buy_fraction > 0
        else 0
    )
    sell_count = (
        max(1, math.ceil(len(descending) * sell_fraction))
        if sell_fraction > 0
        else 0
    )
    buy_symbols = {
        str(row["symbol"])
        for row, score in descending[:buy_count]
        if score > min_buy_score
    }
    sell_symbols = {
        str(row["symbol"])
        for row, score in descending[-sell_count:]
        if score < max_sell_score
    }

    output: list[StrategyDecision] = []
    for row, model_score in scored:
        geometry = _geometry(row, "q1")
        symbol = str(row["symbol"])
        action = "HOLD"
        gross_edge = 0.0
        reward_risk = 0.0

        if (
            symbol in buy_symbols
            and geometry["long_rr"] >= min_rr
            and _net(geometry["up"], global_cfg) >= min_net
        ):
            action = "BUY"
            gross_edge = geometry["up"]
            reward_risk = geometry["long_rr"]
        elif (
            symbol in sell_symbols
            and geometry["short_rr"] >= min_rr
            and _net(geometry["down"], global_cfg) >= min_net
        ):
            action = "SELL"
            gross_edge = geometry["down"]
            reward_risk = geometry["short_rr"]

        if action == "HOLD" or not _liquid(row, strategy_cfg):
            output.append(
                _hold(
                    "weekly_opportunity_ridge",
                    row,
                    "q1",
                    (
                        f"HOLD: weekly score={model_score:.4f} is not a "
                        "cost-valid tail opportunity"
                    ),
                )
            )
            continue

        if action == "BUY":
            stop = geometry["floor"] * (1 - buffer)
            take_profit = geometry["ceiling"]
        else:
            stop = geometry["ceiling"] * (1 + buffer)
            take_profit = geometry["floor"]

        qty = _size(row, strategy_cfg, global_cfg, stop)
        if qty <= 0:
            output.append(
                _hold(
                    "weekly_opportunity_ridge",
                    row,
                    "q1",
                    "HOLD: zero risk-sized quantity",
                )
            )
            continue

        model_scale = min(abs(model_score), 3.0) / 3.0
        score = (
            max(0.0, _net(gross_edge, global_cfg))
            * min(reward_risk, 3.0)
            * model_scale
        )
        output.append(
            StrategyDecision(
                strategy_id="weekly_opportunity_ridge",
                symbol=symbol,
                side=action,
                score=score,
                qty=qty,
                horizon="q1",
                entry_reason=(
                    f"{action}: weekly Ridge tail score={model_score:.4f}, "
                    f"rr={reward_risk:.2f}, "
                    f"net_edge={_net(gross_edge, global_cfg):.2%}"
                ),
                exit_reason="Q1 anchor or ten-session timeout",
                stop_price=stop,
                take_profit_price=take_profit,
                expected_return=0.0,
                expected_range=max(
                    0.0,
                    geometry["ceiling"] - geometry["floor"],
                ),
                timing_alignment=0.5,
            )
        )

    return output
