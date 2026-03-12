from __future__ import annotations

from strategies.base import (
    StrategyDecision,
    _safe_float,
    common_entry_guards,
    position_size_from_risk,
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
        qty = position_size_from_risk(row, strategy_cfg, global_cfg)
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
                entry_reason=f"Model expected_return_d1={ret:.4f}, breach_prob_d1={breach:.3f}; {reason}",
                exit_reason="Exit by target anchor or invalidation anchor",
                stop_price=_safe_float(row.get("floor_d1")) if side == "BUY" else _safe_float(row.get("ceiling_d1")),
                take_profit_price=_safe_float(row.get("ceiling_d1")) if side == "BUY" else _safe_float(row.get("floor_d1")),
                expected_return=ret,
                expected_range=_safe_float(row.get("expected_range_d1")),
                timing_alignment=timing_alignment_score(row, session, strategy_cfg),
            )
        )
    return out
