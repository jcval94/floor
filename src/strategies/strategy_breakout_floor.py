from __future__ import annotations

from strategies.base import (
    StrategyDecision,
    _safe_float,
    adjusted_position_size_from_risk,
    common_entry_guards,
    m3_context_for_decision,
    timing_alignment_score,
)


def generate_breakout_floor_orders(rows: list[dict], global_cfg: dict, strategy_cfg: dict, session: str) -> list[StrategyDecision]:
    out: list[StrategyDecision] = []
    min_momentum = _safe_float(strategy_cfg["entry"]["min_momentum_20"])
    min_conf = _safe_float(strategy_cfg["entry"]["min_confidence_score"])

    for row in rows:
        momentum = _safe_float(row.get("momentum_20"))
        conf = _safe_float(row.get("confidence_score"))
        if momentum < min_momentum or conf < min_conf:
            continue

        side = "BUY"
        ok, reason = common_entry_guards(row, side, global_cfg, strategy_cfg)
        if not ok:
            continue
        m3_ok, m3_reason, m3_ctx = m3_context_for_decision(row, side, global_cfg, strategy_cfg)
        if not m3_ok:
            continue
        qty = adjusted_position_size_from_risk(row, strategy_cfg, global_cfg, _safe_float(m3_ctx.get("size_multiplier"), 1.0))
        if qty <= 0:
            continue

        floor = _safe_float(row.get("floor_d1"))
        stop_buffer = _safe_float(strategy_cfg["risk"]["floor_buffer_pct"])
        stop = floor * (1 - stop_buffer)
        score = 0.5 * conf + 0.5 * momentum

        out.append(
            StrategyDecision(
                strategy_id="breakout_protected_by_floor",
                symbol=str(row["symbol"]),
                side=side,
                score=score,
                qty=qty,
                horizon="d1",
                entry_reason=(
                    f"Breakout setup with momentum={momentum:.3f}, protected by floor anchor; {reason}; "
                    f"m3_context={m3_ctx}; m3_check={m3_reason}"
                ),
                exit_reason="Take profit near expected ceiling or close by session timeout",
                stop_price=stop,
                take_profit_price=_safe_float(row.get("ceiling_d1")),
                expected_return=_safe_float(row.get("expected_return_d1")),
                expected_range=_safe_float(row.get("expected_range_d1")),
                timing_alignment=timing_alignment_score(row, session, strategy_cfg),
                m3_context=m3_ctx,
                priority_adjustment=int(m3_ctx.get("priority_adjustment", 0) or 0),
            )
        )
    return out
