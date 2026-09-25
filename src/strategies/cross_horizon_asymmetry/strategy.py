from __future__ import annotations

from strategies.base import StrategyDecision
from strategies.common import (
    alpha_hurdle,
    apply_m3_context,
    geometry,
    hold_decision,
    liquidity_ok,
    payoff_room_clears_cost,
    risk_sized_qty,
    to_float,
)

STRATEGY_ID = "cross_horizon_asymmetry"


def generate_cross_horizon_orders(
    rows: list[dict],
    global_cfg: dict,
    strategy_cfg: dict,
    session: str,
) -> list[StrategyDecision]:
    del session

    entry = strategy_cfg.get("entry", {})
    min_ratio = to_float(entry.get("min_asymmetry_ratio"), 1.35)
    min_trend = to_float(entry.get("min_abs_trend_score"), 0.005)

    weights = {
        "d1": to_float(entry.get("d1_weight"), 0.2),
        "w1": to_float(entry.get("w1_weight"), 0.3),
        "q1": to_float(entry.get("q1_weight"), 0.5),
    }
    weight_total = sum(weights.values()) or 1.0
    weights = {
        horizon: value / weight_total
        for horizon, value in weights.items()
    }

    momentum_weight = to_float(entry.get("momentum_weight"), 0.6)
    relative_strength_weight = to_float(
        entry.get("relative_strength_weight"),
        0.4,
    )
    buffer = to_float(
        strategy_cfg.get("risk", {}).get("stop_buffer_pct"),
        0.0,
    )

    output: list[StrategyDecision] = []
    for row in rows:
        geometries = {
            horizon: geometry(row, horizon)
            for horizon in weights
        }
        incomplete = any(
            current_geometry["close"] <= 0
            or current_geometry["floor"] <= 0
            or current_geometry["ceiling"] <= 0
            for current_geometry in geometries.values()
        )
        if incomplete or not liquidity_ok(row, strategy_cfg):
            output.append(
                hold_decision(
                    STRATEGY_ID,
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
            momentum_weight * to_float(row.get("momentum_20"))
            + relative_strength_weight * to_float(row.get("rel_strength_20"))
        )
        long_alpha_ok, long_alpha = alpha_hurdle(
            max(0.0, trend),
            global_cfg,
            strategy_cfg,
        )
        short_alpha_ok, short_alpha = alpha_hurdle(
            max(0.0, -trend),
            global_cfg,
            strategy_cfg,
        )

        action = "HOLD"
        payoff_room = 0.0
        reward_risk = 0.0
        alpha = long_alpha
        if (
            long_ratio >= min_ratio
            and trend >= min_trend
            and payoff_room_clears_cost(weighted_up, global_cfg, strategy_cfg)
            and long_alpha_ok
        ):
            action = "BUY"
            payoff_room = weighted_up
            reward_risk = long_ratio
            alpha = long_alpha
        elif (
            short_ratio >= min_ratio
            and trend <= -min_trend
            and payoff_room_clears_cost(weighted_down, global_cfg, strategy_cfg)
            and short_alpha_ok
        ):
            action = "SELL"
            payoff_room = weighted_down
            reward_risk = short_ratio
            alpha = short_alpha

        if action == "HOLD":
            output.append(
                hold_decision(
                    STRATEGY_ID,
                    row,
                    "q1",
                    (
                        "HOLD: asymmetry is only payoff geometry; directional "
                        f"alpha does not clear costs (long={long_ratio:.2f}, "
                        f"short={short_ratio:.2f}, trend={trend:.4f})"
                    ),
                )
            )
            continue

        m3_ok, size_multiplier, priority, m3_context = apply_m3_context(
            row,
            action,
            global_cfg,
        )
        if not m3_ok:
            hold = hold_decision(
                STRATEGY_ID,
                row,
                "q1",
                "HOLD: reliable M3 floor timing blocks tactical BUY",
            )
            hold.m3_context = m3_context
            output.append(hold)
            continue

        q1 = geometries["q1"]
        if action == "BUY":
            stop = q1["risk_floor"] * (1 - buffer)
            take_profit = q1["ceiling"]
            expected_return = alpha["gross_alpha_pct"]
        else:
            stop = q1["risk_ceiling"] * (1 + buffer)
            take_profit = q1["floor"]
            expected_return = -alpha["gross_alpha_pct"]

        qty = risk_sized_qty(
            row,
            strategy_cfg,
            global_cfg,
            stop,
            size_multiplier,
        )
        if qty <= 0:
            output.append(
                hold_decision(
                    STRATEGY_ID,
                    row,
                    "q1",
                    "HOLD: zero risk-sized quantity",
                )
            )
            continue

        # Range coverage is a calibration property of the risk envelope, not
        # directional conviction.  Never let a wider/better-calibrated interval
        # mechanically raise a trading score.  Directional ranking stays driven
        # by net alpha after costs and realized payoff asymmetry.
        score = (
            max(0.0, alpha["net_alpha_pct"])
            * min(reward_risk, 3.0)
        )
        output.append(
            StrategyDecision(
                strategy_id=STRATEGY_ID,
                symbol=str(row["symbol"]),
                side=action,
                score=score,
                qty=qty,
                horizon="q1",
                entry_reason=(
                    f"{action}: cross-horizon payoff asymmetry={reward_risk:.2f}, "
                    f"trend-alpha={alpha['gross_alpha_pct']:.2%}, "
                    f"net_alpha={alpha['net_alpha_pct']:.2%}, "
                    f"payoff_room={payoff_room:.2%}"
                ),
                exit_reason="Q1 anchor or ten-session timeout",
                stop_price=stop,
                take_profit_price=take_profit,
                expected_return=expected_return,
                expected_range=max(0.0, q1["ceiling"] - q1["floor"]),
                timing_alignment=0.5,
                m3_context=m3_context,
                priority_adjustment=priority,
                gross_alpha_pct=alpha["gross_alpha_pct"],
                net_alpha_pct=alpha["net_alpha_pct"],
                cost_pct=alpha["cost_pct"],
                alpha_source="momentum_relative_strength_trend_proxy",
                payoff_room_pct=payoff_room,
            )
        )

    return output
