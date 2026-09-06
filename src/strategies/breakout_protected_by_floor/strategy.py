from __future__ import annotations

from strategies.base import StrategyDecision
from strategies.common import (
    apply_m3_context,
    geometry,
    hold_decision,
    liquidity_ok,
    net_edge,
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
    min_net = to_float(entry.get("min_net_edge_pct"), 0.003)
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

        action = "HOLD"
        gross_edge = 0.0
        reward_risk = 0.0
        if (
            trend >= min_trend
            and current_geometry["long_rr"] >= min_rr
            and net_edge(current_geometry["up"], global_cfg) >= min_net
        ):
            action = "BUY"
            gross_edge = current_geometry["up"]
            reward_risk = current_geometry["long_rr"]
        elif (
            trend <= -min_trend
            and current_geometry["short_rr"] >= min_rr
            and net_edge(current_geometry["down"], global_cfg) >= min_net
        ):
            action = "SELL"
            gross_edge = current_geometry["down"]
            reward_risk = current_geometry["short_rr"]

        if action == "HOLD" or not liquidity_ok(row, strategy_cfg):
            output.append(
                hold_decision(
                    STRATEGY_ID,
                    row,
                    "d1",
                    (
                        "HOLD: trend/range does not clear cost-adjusted gate "
                        f"(trend={trend:.4f})"
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
            stop = current_geometry["floor"] * (1 - buffer)
            take_profit = current_geometry["ceiling"]
        else:
            stop = current_geometry["ceiling"] * (1 + buffer)
            take_profit = current_geometry["floor"]

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

        confidence = max(
            0.0,
            min(1.0, to_float(row.get("confidence_score"), 0.5)),
        )
        score = (
            max(0.0, net_edge(gross_edge, global_cfg))
            * min(reward_risk, 3.0)
            * confidence
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
                    f"{action}: trend={trend:.4f}, rr={reward_risk:.2f}, "
                    f"net_edge={net_edge(gross_edge, global_cfg):.2%} after "
                    f"{round_trip_cost_bps(global_cfg):.0f} bps round-trip"
                ),
                exit_reason="D1 floor/ceiling or one-session timeout",
                stop_price=stop,
                take_profit_price=take_profit,
                expected_return=0.0,
                expected_range=max(
                    0.0,
                    current_geometry["ceiling"] - current_geometry["floor"],
                ),
                timing_alignment=0.5,
                m3_context=m3_context,
                priority_adjustment=priority,
            )
        )

    return output
