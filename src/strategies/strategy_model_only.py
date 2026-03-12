from __future__ import annotations

from strategies.base import (
    StrategyDecision,
    _safe_float,
    adjusted_position_size_from_risk,
    common_entry_guards,
    m3_context_for_decision,
    timing_alignment_score,
)


def generate_model_only_orders(rows: list[dict], global_cfg: dict, strategy_cfg: dict, session: str) -> list[StrategyDecision]:
    out: list[StrategyDecision] = []
    min_return = _safe_float(strategy_cfg["entry"]["min_expected_return_d1"])
    max_breach = _safe_float(strategy_cfg["entry"]["max_breach_prob_d1"])

    for row in rows:
        ret = _safe_float(row.get("expected_return_d1"))
        breach = _safe_float(row.get("breach_prob_d1"))
        if abs(ret) < min_return or breach > max_breach:
            continue
        side = "BUY" if ret >= 0 else "SELL"
        ok, reason = common_entry_guards(row, side, global_cfg, strategy_cfg)
        if not ok:
            continue
        m3_ok, m3_reason, m3_ctx = m3_context_for_decision(row, side, global_cfg, strategy_cfg)
        if not m3_ok:
            continue
        qty = adjusted_position_size_from_risk(row, strategy_cfg, global_cfg, _safe_float(m3_ctx.get("size_multiplier"), 1.0))
        if qty <= 0:
            continue
        out.append(
            StrategyDecision(
                strategy_id="model_only",
                symbol=str(row["symbol"]),
                side=side,
                score=abs(ret) * (1 - breach),
                qty=qty,
                horizon="d1",
                entry_reason=(
                    f"Model expected_return_d1={ret:.4f}, breach_prob_d1={breach:.3f}; {reason}; "
                    f"m3_context={m3_ctx}; m3_check={m3_reason}"
                ),
                exit_reason="Exit by target anchor or invalidation anchor",
                stop_price=_safe_float(row.get("floor_d1")) if side == "BUY" else _safe_float(row.get("ceiling_d1")),
                take_profit_price=_safe_float(row.get("ceiling_d1")) if side == "BUY" else _safe_float(row.get("floor_d1")),
                expected_return=ret,
                expected_range=_safe_float(row.get("expected_range_d1")),
                timing_alignment=timing_alignment_score(row, session, strategy_cfg),
                m3_context=m3_ctx,
                priority_adjustment=int(m3_ctx.get("priority_adjustment", 0) or 0),
            )
        )
    return out
