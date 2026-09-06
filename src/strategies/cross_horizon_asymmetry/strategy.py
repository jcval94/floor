from __future__ import annotations

from strategies.base import StrategyDecision
from strategies.common import (
    apply_m3_context,
    geometry,
    hold_decision,
    liquidity_ok,
    net_edge,
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
    min_net = to_float(entry.get("min_net_edge_pct"), 0.004)

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

        action = "HOLD"
        gross_edge = 0.0
        reward_risk = 0.0
        if (
            long_ratio >= min_ratio
            and trend >= min_trend
            and net_edge(weighted_up, global_cfg) >= min_net
        ):
            action = "BUY"
            gross_edge = weighted_up
            reward_risk = long_ratio
        elif (
            short_ratio >= min_ratio
            and trend <= -min_trend
            and net_edge(weighted_down, global_cfg) >= min_net
        ):
            action = "SELL"
            gross_edge = weighted_down
            reward_risk = short_ratio

        if action == "HOLD":
            output.append(
                hold_decision(
                    STRATEGY_ID,
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
            stop = q1["floor"] * (1 - buffer)
            take_profit = q1["ceiling"]
        else:
            stop = q1["ceiling"] * (1 + buffer)
            take_profit = q1["floor"]

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
                horizon="q1",
                entry_reason=(
                    f"{action}: cross-horizon asymmetry={reward_risk:.2f}, "
                    f"trend={trend:.4f}, "
                    f"net_edge={net_edge(gross_edge, global_cfg):.2%}"
                ),
                exit_reason="Q1 anchor or ten-session timeout",
                stop_price=stop,
                take_profit_price=take_profit,
                expected_return=0.0,
                expected_range=max(0.0, q1["ceiling"] - q1["floor"]),
                timing_alignment=0.5,
                m3_context=m3_context,
                priority_adjustment=priority,
            )
        )

    return output
