from __future__ import annotations

from strategies.base import (
    StrategyDecision,
    _safe_float,
    adjusted_position_size_from_risk,
    common_entry_guards,
    m3_context_for_decision,
    timing_alignment_score,
)


def generate_mean_reversion_orders(rows: list[dict], global_cfg: dict, strategy_cfg: dict, session: str) -> list[StrategyDecision]:
    out: list[StrategyDecision] = []
    near_floor_pct = _safe_float(strategy_cfg["entry"]["near_floor_w1_pct"])

    for row in rows:
        close = _safe_float(row.get("close"), 0.0)
        floor_w1 = _safe_float(row.get("floor_w1"), 0.0)
        if close <= 0 or floor_w1 <= 0:
            continue
        dist = (close - floor_w1) / close
        if dist > near_floor_pct:
            continue
        if _safe_float(row.get("expected_return_w1"), 0.0) <= 0:
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

        score = max(0.0, (near_floor_pct - dist) / max(near_floor_pct, 1e-9))
        out.append(
            StrategyDecision(
                strategy_id="mean_reversion_floor_w1",
                symbol=str(row["symbol"]),
                side=side,
                score=score,
                qty=qty,
                horizon="w1",
                entry_reason=(
                    f"Price near weekly floor (dist={dist:.4f}) and positive w1 return; {reason}; "
                    f"m3_context={m3_ctx}; m3_check={m3_reason}"
                ),
                exit_reason="Take profit at weekly ceiling or timeout at w1 horizon",
                stop_price=floor_w1,
                take_profit_price=_safe_float(row.get("ceiling_w1")),
                expected_return=_safe_float(row.get("expected_return_w1")),
                expected_range=_safe_float(row.get("expected_range_w1")),
                timing_alignment=timing_alignment_score(row, session, strategy_cfg),
                m3_context=m3_ctx,
                priority_adjustment=int(m3_ctx.get("priority_adjustment", 0) or 0),
            )
        )
    return out
