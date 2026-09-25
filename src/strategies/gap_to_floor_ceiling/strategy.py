from __future__ import annotations

from strategies.base import StrategyDecision
from strategies.common import (
    alpha_hurdle,
    apply_m3_context,
    hold_decision,
    liquidity_ok,
    payoff_room_clears_cost,
    risk_sized_qty,
    round_trip_cost_bps,
    to_float,
)

STRATEGY_ID = "gap_to_floor_ceiling"


def _open_geometry(row: dict) -> dict[str, float | bool]:
    open_price = to_float(row.get("open"))
    floor = to_float(row.get("floor_d1"))
    ceiling = to_float(row.get("ceiling_d1"))
    risk_available = bool(row.get("risk_geometry_available_d1", False))
    risk_floor = to_float(row.get("risk_floor_d1"))
    risk_ceiling = to_float(row.get("risk_ceiling_d1"))

    if (
        open_price <= 0
        or floor <= 0
        or ceiling <= floor
        or not risk_available
        or risk_floor <= 0
        or risk_ceiling <= risk_floor
    ):
        return {
            "valid": False,
            "open": open_price,
            "floor": floor,
            "ceiling": ceiling,
            "risk_floor": risk_floor,
            "risk_ceiling": risk_ceiling,
            "up": 0.0,
            "down": 0.0,
            "long_rr": 0.0,
            "short_rr": 0.0,
        }

    up = max(0.0, ceiling - open_price) / open_price
    down = max(0.0, open_price - floor) / open_price
    long_risk = max(0.0, open_price - risk_floor) / open_price
    short_risk = max(0.0, risk_ceiling - open_price) / open_price
    return {
        "valid": True,
        "open": open_price,
        "floor": floor,
        "ceiling": ceiling,
        "risk_floor": risk_floor,
        "risk_ceiling": risk_ceiling,
        "up": up,
        "down": down,
        "long_rr": up / max(long_risk, 1e-9),
        "short_rr": down / max(short_risk, 1e-9),
    }


def generate_gap_to_floor_ceiling_orders(
    rows: list[dict],
    global_cfg: dict,
    strategy_cfg: dict,
    session: str,
) -> list[StrategyDecision]:
    """Trade opening gaps only when they land near calibrated D1 anchors.

    The opening gap and Floor/Ceiling geometry define the event and risk space,
    not expected alpha. A trade requires an explicit point-in-time directional
    alpha estimate. The strategy is intentionally valid only at OPEN.
    """

    entry = strategy_cfg.get("entry", {})
    min_gap = max(0.0, to_float(entry.get("min_abs_gap_pct"), 0.015))
    anchor_tolerance = max(
        0.0,
        to_float(entry.get("anchor_tolerance_pct"), 0.015),
    )
    min_rr = max(0.0, to_float(entry.get("min_reward_risk"), 1.25))
    buffer = max(
        0.0,
        to_float(strategy_cfg.get("risk", {}).get("stop_buffer_pct"), 0.002),
    )

    output: list[StrategyDecision] = []
    for row in rows:
        if str(session).upper() != "OPEN":
            output.append(
                hold_decision(
                    STRATEGY_ID,
                    row,
                    "d1",
                    "HOLD: opening-gap challenger only evaluates at OPEN",
                )
            )
            continue

        current = _open_geometry(row)
        if not current["valid"]:
            output.append(
                hold_decision(
                    STRATEGY_ID,
                    row,
                    "d1",
                    "HOLD: calibrated D1 risk geometry unavailable at OPEN",
                )
            )
            continue

        if not liquidity_ok(row, strategy_cfg):
            output.append(
                hold_decision(
                    STRATEGY_ID,
                    row,
                    "d1",
                    "HOLD: liquidity below gap challenger minimum",
                )
            )
            continue

        open_price = float(current["open"])
        floor = float(current["floor"])
        ceiling = float(current["ceiling"])
        risk_floor = float(current["risk_floor"])
        risk_ceiling = float(current["risk_ceiling"])
        gap = to_float(row.get("gap_open_to_prev_close"), 0.0)

        if open_price <= risk_floor or open_price >= risk_ceiling:
            output.append(
                hold_decision(
                    STRATEGY_ID,
                    row,
                    "d1",
                    "HOLD: opening gap breached calibrated risk boundary",
                )
            )
            continue

        floor_distance = abs(open_price - floor) / open_price
        ceiling_distance = abs(ceiling - open_price) / open_price

        if gap <= -min_gap and floor_distance <= anchor_tolerance:
            action = "BUY"
            alpha_field = "gap_floor_alpha_pct"
            gross_alpha = to_float(row.get(alpha_field), 0.0)
            reward_risk = float(current["long_rr"])
            payoff_room = float(current["up"])
            stop = risk_floor * (1.0 - buffer)
            take_profit = ceiling
        elif gap >= min_gap and ceiling_distance <= anchor_tolerance:
            action = "SELL"
            alpha_field = "gap_ceiling_alpha_pct"
            gross_alpha = to_float(row.get(alpha_field), 0.0)
            reward_risk = float(current["short_rr"])
            payoff_room = float(current["down"])
            stop = risk_ceiling * (1.0 + buffer)
            take_profit = floor
        else:
            output.append(
                hold_decision(
                    STRATEGY_ID,
                    row,
                    "d1",
                    (
                        f"HOLD: gap={gap:.2%} does not land near the "
                        "corresponding D1 floor/ceiling"
                    ),
                )
            )
            continue

        if gross_alpha <= 0:
            output.append(
                hold_decision(
                    STRATEGY_ID,
                    row,
                    "d1",
                    (
                        f"HOLD: {action} gap event observed but explicit "
                        f"{alpha_field} is unavailable"
                    ),
                )
            )
            continue

        alpha_ok, alpha = alpha_hurdle(
            gross_alpha,
            global_cfg,
            strategy_cfg,
        )
        if (
            not alpha_ok
            or reward_risk < min_rr
            or not payoff_room_clears_cost(payoff_room, global_cfg, strategy_cfg)
        ):
            output.append(
                hold_decision(
                    STRATEGY_ID,
                    row,
                    "d1",
                    (
                        "HOLD: opening gap does not clear directional alpha, "
                        "payoff-room and reward/risk gates"
                    ),
                )
            )
            continue

        entry_row = dict(row)
        entry_row["close"] = open_price
        m3_ok, size_multiplier, priority, m3_context = apply_m3_context(
            entry_row,
            action,
            global_cfg,
        )
        if not m3_ok:
            hold = hold_decision(
                STRATEGY_ID,
                row,
                "d1",
                "HOLD: reliable M3 context blocks tactical opening-gap BUY",
            )
            hold.m3_context = m3_context
            output.append(hold)
            continue

        qty = risk_sized_qty(
            entry_row,
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
                    "HOLD: zero risk-sized quantity at opening price",
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
        expected_return = (
            alpha["gross_alpha_pct"]
            if action == "BUY"
            else -alpha["gross_alpha_pct"]
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
                    f"{action}: OPEN gap={gap:.2%} near "
                    f"{'floor' if action == 'BUY' else 'ceiling'}, "
                    f"alpha={alpha['gross_alpha_pct']:.2%}, "
                    f"net_alpha={alpha['net_alpha_pct']:.2%}, "
                    f"payoff_room={payoff_room:.2%}, rr={reward_risk:.2f}, "
                    f"cost={round_trip_cost_bps(global_cfg):.0f} bps"
                ),
                exit_reason=(
                    "Opposite D1 anchor, calibrated risk boundary or "
                    "one-session timeout"
                ),
                stop_price=stop,
                take_profit_price=take_profit,
                expected_return=expected_return,
                expected_range=max(0.0, ceiling - floor),
                timing_alignment=1.0,
                m3_context=m3_context,
                priority_adjustment=priority,
                gross_alpha_pct=alpha["gross_alpha_pct"],
                net_alpha_pct=alpha["net_alpha_pct"],
                cost_pct=alpha["cost_pct"],
                alpha_source=alpha_field,
                payoff_room_pct=payoff_room,
            )
        )

    return output
