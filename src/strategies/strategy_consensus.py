from __future__ import annotations

from strategies.base import (
    StrategyDecision,
    _safe_float,
    common_entry_guards,
    position_size_from_risk,
    timing_alignment_score,
)


def generate_consensus_orders(rows: list[dict], global_cfg: dict, strategy_cfg: dict, session: str) -> list[StrategyDecision]:
    out: list[StrategyDecision] = []
    min_consensus = _safe_float(strategy_cfg["entry"]["min_consensus_score"])
    for row in rows:
        ai = _safe_float(row.get("ai_alignment_score"))
        model = _safe_float(row.get("composite_signal_score"))
        score = (ai + model) / 2
        if abs(score) < min_consensus:
            continue
        side = "BUY" if score >= 0 else "SELL"
        ok, reason = common_entry_guards(row, side, global_cfg, strategy_cfg)
        if not ok:
            continue
        qty = position_size_from_risk(row, strategy_cfg, global_cfg)
        if qty <= 0:
            continue
        out.append(
            StrategyDecision(
                strategy_id="consensus",
                symbol=str(row["symbol"]),
                side=side,
                score=score,
                qty=qty,
                horizon="d1",
                entry_reason=f"Consensus score {score:.3f} (ai={ai:.3f}, model={model:.3f}); {reason}",
                exit_reason="Exit at expected anchor or temporal cutoff",
                stop_price=_safe_float(row.get("floor_d1")) if side == "BUY" else _safe_float(row.get("ceiling_d1")),
                take_profit_price=_safe_float(row.get("ceiling_d1")) if side == "BUY" else _safe_float(row.get("floor_d1")),
                expected_return=_safe_float(row.get("expected_return_d1")),
                expected_range=_safe_float(row.get("expected_range_d1")),
                timing_alignment=timing_alignment_score(row, session, strategy_cfg),
            )
        )
    return out
