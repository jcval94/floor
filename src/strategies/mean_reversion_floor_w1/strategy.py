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

STRATEGY_ID = "mean_reversion_floor_w1"


def generate_mean_reversion_orders(
    rows: list[dict],
    global_cfg: dict,
    strategy_cfg: dict,
    session: str,
) -> list[StrategyDecision]:
    del session

    entry = strategy_cfg.get("entry", {})
    near_anchor = to_float(entry.get("near_anchor_pct"), 0.02)
    min_rr = to_float(entry.get("min_reward_risk"), 1.30)
    min_reversal = max(0.0, to_float(entry.get("min_reversal_signal_pct"), 0.0))
    buffer = to_float(
        strategy_cfg.get("risk", {}).get("stop_buffer_pct"),
        0.0,
    )

    output: list[StrategyDecision] = []
    for row in rows:
        current_geometry = geometry(row, "w1")
        close = current_geometry["close"]
        momentum_10 = to_float(row.get("momentum_10"))
        momentum_20 = to_float(row.get("momentum_20"))
        reversal_signal = momentum_10 - momentum_20

        if close <= 0 or not liquidity_ok(row, strategy_cfg):
            output.append(
                hold_decision(
                    STRATEGY_ID,
                    row,
                    "w1",
                    "HOLD: invalid geometry/liquidity",
                )
            )
            continue

        floor_distance = max(0.0, close - current_geometry["floor"]) / close
        ceiling_distance = max(0.0, current_geometry["ceiling"] - close) / close
        long_alpha_ok, long_alpha = alpha_hurdle(
            max(0.0, reversal_signal),
            global_cfg,
            strategy_cfg,
        )
        short_alpha_ok, short_alpha = alpha_hurdle(
            max(0.0, -reversal_signal),
            global_cfg,
            strategy_cfg,
        )
        liquid = liquidity_ok(row, strategy_cfg)
        long_payoff_ok = payoff_room_clears_cost(
            current_geometry["up"], global_cfg, strategy_cfg
        )
        short_payoff_ok = payoff_room_clears_cost(
            current_geometry["down"], global_cfg, strategy_cfg
        )
        trace = {
            "inputs": {
                "floor_distance_pct": floor_distance,
                "ceiling_distance_pct": ceiling_distance,
                "momentum_10": momentum_10,
                "momentum_20": momentum_20,
                "reversal_signal_pct": reversal_signal,
                "opportunity_long_rr": current_geometry["opportunity_long_rr"],
                "opportunity_short_rr": current_geometry["opportunity_short_rr"],
                "risk_long_rr": current_geometry["risk_long_rr"],
                "risk_short_rr": current_geometry["risk_short_rr"],
                "upside_pct": current_geometry["up"],
                "downside_pct": current_geometry["down"],
            },
            "thresholds": {
                "near_anchor_pct": near_anchor,
                "min_reversal_signal_pct": min_reversal,
                "min_opportunity_rr": min_rr,
                "required_long_gross_alpha_pct": long_alpha["required_gross_alpha_pct"],
                "required_short_gross_alpha_pct": short_alpha["required_gross_alpha_pct"],
            },
            "gates": {
                "liquidity": liquid,
                "long_near_floor": floor_distance <= near_anchor,
                "long_reversal": reversal_signal >= min_reversal,
                "long_opportunity_rr": current_geometry["opportunity_long_rr"] >= min_rr,
                "long_payoff_after_costs": long_payoff_ok,
                "long_alpha": long_alpha_ok,
                "short_near_ceiling": ceiling_distance <= near_anchor,
                "short_reversal": reversal_signal <= -min_reversal,
                "short_opportunity_rr": current_geometry["opportunity_short_rr"] >= min_rr,
                "short_payoff_after_costs": short_payoff_ok,
                "short_alpha": short_alpha_ok,
            },
        }
        candidates: list[tuple[str, float, float, float, dict[str, float]]] = []

        if (
            floor_distance <= near_anchor
            and reversal_signal >= min_reversal
            and current_geometry["opportunity_long_rr"] >= min_rr
            and long_payoff_ok
            and long_alpha_ok
        ):
            candidates.append(
                (
                    "BUY",
                    max(0.0, long_alpha["net_alpha_pct"])
                    * min(current_geometry["opportunity_long_rr"], 3.0),
                    current_geometry["up"],
                    current_geometry["long_rr"],
                    long_alpha,
                )
            )

        if (
            ceiling_distance <= near_anchor
            and reversal_signal <= -min_reversal
            and current_geometry["opportunity_short_rr"] >= min_rr
            and short_payoff_ok
            and short_alpha_ok
        ):
            candidates.append(
                (
                    "SELL",
                    max(0.0, short_alpha["net_alpha_pct"])
                    * min(current_geometry["opportunity_short_rr"], 3.0),
                    current_geometry["down"],
                    current_geometry["short_rr"],
                    short_alpha,
                )
            )

        if not candidates:
            output.append(
                hold_decision(
                    STRATEGY_ID,
                    row,
                    "w1",
                    (
                        "HOLD: no cost-valid W1 reversal with confirmed "
                        f"directional recovery (floor_dist={floor_distance:.2%}, "
                        f"ceiling_dist={ceiling_distance:.2%}, "
                        f"momentum10={momentum_10:.2%}, "
                        f"momentum20={momentum_20:.2%}, "
                        f"reversal={reversal_signal:.2%}, "
                        f"opp_rr_long={current_geometry['opportunity_long_rr']:.2f}, "
                        f"opp_rr_short={current_geometry['opportunity_short_rr']:.2f}, "
                        f"risk_rr_long={current_geometry['risk_long_rr']:.2f}, "
                        f"risk_rr_short={current_geometry['risk_short_rr']:.2f}, "
                        f"required_opp_rr={min_rr:.2f})"
                    ),
                    trace=trace,
                )
            )
            continue

        action, score, payoff_room, reward_risk, alpha = max(
            candidates,
            key=lambda item: item[1],
        )
        m3_ok, size_multiplier, priority, m3_context = apply_m3_context(
            row,
            action,
            global_cfg,
        )
        if not m3_ok:
            hold = hold_decision(
                STRATEGY_ID,
                row,
                "w1",
                "HOLD: reliable M3 floor timing blocks tactical BUY",
            )
            hold.m3_context = m3_context
            hold.decision_trace = {
                **trace,
                "m3": {"passed": False, "context": m3_context},
            }
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
                    "w1",
                    "HOLD: zero risk-sized quantity",
                    trace={**trace, "selected_action": action},
                )
            )
            continue

        output.append(
            StrategyDecision(
                strategy_id=STRATEGY_ID,
                symbol=str(row["symbol"]),
                side=action,
                score=score,
                qty=qty,
                horizon="w1",
                entry_reason=(
                    f"{action}: W1 reversal confirmed by 10d-vs-20d momentum "
                    f"alpha={alpha['gross_alpha_pct']:.2%}, "
                    f"net_alpha={alpha['net_alpha_pct']:.2%}, "
                    f"payoff_room={payoff_room:.2%}, opp_rr={reward_risk:.2f}, "
                    f"risk_rr={current_geometry['risk_long_rr' if action == 'BUY' else 'risk_short_rr']:.2f}"
                ),
                exit_reason="Opposite W1 anchor or five-session timeout",
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
                alpha_source="momentum_10_minus_momentum_20_reversal",
                payoff_room_pct=payoff_room,
                decision_trace={
                    **trace,
                    "m3": {"passed": True, "context": m3_context},
                    "selected_action": action,
                },
            )
        )

    return output
