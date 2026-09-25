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
    round_trip_cost_bps,
    to_float,
)

STRATEGY_ID = "breakout_protected_by_floor"


def generate_breakout_floor_orders(
    rows: list[dict],
    global_cfg: dict,
    strategy_cfg: dict,
    session: str,
) -> list[StrategyDecision]:
    del session

    entry = strategy_cfg.get("entry", {})
    min_trend = to_float(entry.get("min_abs_trend_score"), 0.01)
    min_rr = to_float(entry.get("min_reward_risk"), 1.25)
    momentum_weight = to_float(entry.get("momentum_weight"), 0.65)
    relative_strength_weight = to_float(
        entry.get("relative_strength_weight"),
        0.35,
    )
    buffer = to_float(
        strategy_cfg.get("risk", {}).get("stop_buffer_pct"),
        0.0,
    )

    output: list[StrategyDecision] = []
    for row in rows:
        current_geometry = geometry(row, "d1")
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
        reward_risk = 0.0
        payoff_room = 0.0
        alpha = long_alpha
        if (
            trend >= min_trend
            and current_geometry["long_rr"] >= min_rr
            and payoff_room_clears_cost(
                current_geometry["up"], global_cfg, strategy_cfg
            )
            and long_alpha_ok
        ):
            action = "BUY"
            reward_risk = current_geometry["long_rr"]
            payoff_room = current_geometry["up"]
            alpha = long_alpha
        elif (
            trend <= -min_trend
            and current_geometry["short_rr"] >= min_rr
            and payoff_room_clears_cost(
                current_geometry["down"], global_cfg, strategy_cfg
            )
            and short_alpha_ok
        ):
            action = "SELL"
            reward_risk = current_geometry["short_rr"]
            payoff_room = current_geometry["down"]
            alpha = short_alpha

        if action == "HOLD" or not liquidity_ok(row, strategy_cfg):
            output.append(
                hold_decision(
                    STRATEGY_ID,
                    row,
                    "d1",
                    (
                        "HOLD: directional alpha/payoff room does not clear "
                        f"cost-adjusted gate (trend={trend:.4f})"
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
                "d1",
                "HOLD: reliable M3 floor timing blocks tactical BUY",
            )
            hold.m3_context = m3_context
            output.append(hold)
            continue

        if action == "BUY":
            stop = current_geometry["risk_floor"] * (1 - buffer)
            take_profit = current_geometry["ceiling"]
            expected_return = alpha["gross_alpha_pct"]
        else:
            stop = current_geometry["risk_ceiling"] * (1 + buffer)
            take_profit = current_geometry["floor"]
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
                    "d1",
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
                horizon="d1",
                entry_reason=(
                    f"{action}: trend-alpha={alpha['gross_alpha_pct']:.2%}, "
                    f"net_alpha={alpha['net_alpha_pct']:.2%}, "
                    f"payoff_room={payoff_room:.2%}, rr={reward_risk:.2f}, "
                    f"cost={round_trip_cost_bps(global_cfg):.0f} bps"
                ),
                exit_reason="D1 floor/ceiling or one-session timeout",
                stop_price=stop,
                take_profit_price=take_profit,
                expected_return=expected_return,
                expected_range=max(
                    0.0,
                    current_geometry["ceiling"] - current_geometry["floor"],
                ),
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
