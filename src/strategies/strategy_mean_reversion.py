from __future__ import annotations

from strategies.base import (
    StrategyDecision,
    _safe_float,
    common_entry_guards,
    position_size_from_risk,
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
        qty = position_size_from_risk(row, strategy_cfg, global_cfg)
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
                entry_reason=f"Price near weekly floor (dist={dist:.4f}) and positive w1 return; {reason}",
                exit_reason="Take profit at weekly ceiling or timeout at w1 horizon",
                stop_price=floor_w1,
                take_profit_price=_safe_float(row.get("ceiling_w1")),
                expected_return=_safe_float(row.get("expected_return_w1")),
                expected_range=_safe_float(row.get("expected_range_w1")),
                timing_alignment=timing_alignment_score(row, session, strategy_cfg),
            )
        )
    return out
