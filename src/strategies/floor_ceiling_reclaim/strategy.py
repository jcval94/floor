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

STRATEGY_ID = "floor_ceiling_reclaim"

_REQUIRED_INTRADAY_FIELDS = (
    "intraday_prev_price",
    "intraday_low",
    "intraday_high",
)


def _intraday_context_available(row: dict) -> bool:
    return all(to_float(row.get(field), 0.0) > 0 for field in _REQUIRED_INTRADAY_FIELDS)


def generate_floor_ceiling_reclaim_orders(
    rows: list[dict],
    global_cfg: dict,
    strategy_cfg: dict,
    session: str,
) -> list[StrategyDecision]:
    """Detect confirmed D1 floor reclaims / ceiling rejections.

    This challenger is intentionally fail-closed. Floor/Ceiling geometry only
    identifies a potential reaction zone; it is never treated as expected alpha.
    A directional trade is emitted only when the caller supplies an explicit,
    point-in-time alpha estimate for the observed reclaim/rejection event.
    """

    del session

    entry = strategy_cfg.get("entry", {})
    touch_tolerance = max(0.0, to_float(entry.get("touch_tolerance_pct"), 0.002))
    confirmation = max(0.0, to_float(entry.get("confirmation_buffer_pct"), 0.001))
    min_rr = max(0.0, to_float(entry.get("min_reward_risk"), 1.25))
    min_relative_volume = max(0.0, to_float(entry.get("min_relative_volume"), 0.0))
    buffer = max(
        0.0,
        to_float(strategy_cfg.get("risk", {}).get("stop_buffer_pct"), 0.002),
    )

    output: list[StrategyDecision] = []
    for row in rows:
        current_geometry = geometry(row, "d1")
        close = current_geometry["close"]

        if close <= 0 or not _intraday_context_available(row):
            output.append(
                hold_decision(
                    STRATEGY_ID,
                    row,
                    "d1",
                    "HOLD: point-in-time intraday reclaim context unavailable",
                )
            )
            continue

        if not liquidity_ok(row, strategy_cfg):
            output.append(
                hold_decision(
                    STRATEGY_ID,
                    row,
                    "d1",
                    "HOLD: liquidity below reclaim challenger minimum",
                )
            )
            continue

        relative_volume = to_float(row.get("relative_volume_20"), 1.0)
        if relative_volume < min_relative_volume:
            output.append(
                hold_decision(
                    STRATEGY_ID,
                    row,
                    "d1",
                    (
                        "HOLD: reclaim confirmation lacks relative volume "
                        f"({relative_volume:.2f} < {min_relative_volume:.2f})"
                    ),
                )
            )
            continue

        previous = to_float(row.get("intraday_prev_price"))
        session_low = to_float(row.get("intraday_low"))
        session_high = to_float(row.get("intraday_high"))
        floor = current_geometry["floor"]
        ceiling = current_geometry["ceiling"]

        floor_touched = floor > 0 and session_low <= floor * (1.0 + touch_tolerance)
        floor_reclaimed = (
            floor_touched
            and previous <= floor
            and close >= floor * (1.0 + confirmation)
        )
        ceiling_touched = ceiling > 0 and session_high >= ceiling * (1.0 - touch_tolerance)
        ceiling_rejected = (
            ceiling_touched
            and previous >= ceiling
            and close <= ceiling * (1.0 - confirmation)
        )

        if floor_reclaimed:
            action = "BUY"
            alpha_field = "floor_reclaim_alpha_pct"
            gross_alpha = to_float(row.get(alpha_field), 0.0)
            reward_risk = current_geometry["long_rr"]
            payoff_room = current_geometry["up"]
            stop = current_geometry["risk_floor"] * (1.0 - buffer)
            take_profit = ceiling
        elif ceiling_rejected:
            action = "SELL"
            alpha_field = "ceiling_rejection_alpha_pct"
            gross_alpha = to_float(row.get(alpha_field), 0.0)
            reward_risk = current_geometry["short_rr"]
            payoff_room = current_geometry["down"]
            stop = current_geometry["risk_ceiling"] * (1.0 + buffer)
            take_profit = floor
        else:
            output.append(
                hold_decision(
                    STRATEGY_ID,
                    row,
                    "d1",
                    "HOLD: no confirmed floor reclaim or ceiling rejection",
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
                        f"HOLD: {action} reclaim event observed but explicit "
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
                        "HOLD: confirmed reclaim/rejection does not clear "
                        "alpha, payoff-room and reward/risk gates"
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
                "HOLD: reliable M3 context blocks tactical reclaim BUY",
            )
            hold.m3_context = m3_context
            output.append(hold)
            continue

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
        signed_expected_return = (
            alpha["gross_alpha_pct"] if action == "BUY" else -alpha["gross_alpha_pct"]
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
                    f"{action}: confirmed D1 "
                    f"{'floor reclaim' if action == 'BUY' else 'ceiling rejection'}, "
                    f"alpha={alpha['gross_alpha_pct']:.2%}, "
                    f"net_alpha={alpha['net_alpha_pct']:.2%}, "
                    f"payoff_room={payoff_room:.2%}, rr={reward_risk:.2f}, "
                    f"cost={round_trip_cost_bps(global_cfg):.0f} bps"
                ),
                exit_reason="Opposite D1 anchor, calibrated risk boundary or one-session timeout",
                stop_price=stop,
                take_profit_price=take_profit,
                expected_return=signed_expected_return,
                expected_range=max(0.0, ceiling - floor),
                timing_alignment=0.5,
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
