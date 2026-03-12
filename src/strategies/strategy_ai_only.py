from __future__ import annotations

from strategies.base import (
    StrategyDecision,
    _safe_float,
    common_entry_guards,
    position_size_from_risk,
    timing_alignment_score,
)


def generate_ai_only_orders(rows: list[dict], global_cfg: dict, strategy_cfg: dict, session: str) -> list[StrategyDecision]:
    out: list[StrategyDecision] = []
    min_score = _safe_float(strategy_cfg["entry"]["min_ai_alignment_score"])
    for row in rows:
        score = _safe_float(row.get("ai_alignment_score"))
        if score < min_score:
            continue
        side = "BUY" if score >= 0 else "SELL"
        ok, reason = common_entry_guards(row, side, global_cfg, strategy_cfg)
        if not ok:
            continue
        qty = position_size_from_risk(row, strategy_cfg, global_cfg)
        if qty <= 0:
            continue
        stop = _safe_float(row.get("floor_d1")) if side == "BUY" else _safe_float(row.get("ceiling_d1"))
        take = _safe_float(row.get("ceiling_d1")) if side == "BUY" else _safe_float(row.get("floor_d1"))
        out.append(
            StrategyDecision(
                strategy_id="ai_only",
                symbol=str(row["symbol"]),
                side=side,
                score=score,
                qty=qty,
                horizon="d1",
                entry_reason=f"AI alignment score {score:.3f}; {reason}",
                exit_reason="Exit at expected ceiling/floor, invalidation at opposite anchor",
                stop_price=stop,
                take_profit_price=take,
                expected_return=_safe_float(row.get("expected_return_d1")),
                expected_range=_safe_float(row.get("expected_range_d1")),
                timing_alignment=timing_alignment_score(row, session, strategy_cfg),
            )
        )
    return out
