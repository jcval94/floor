from __future__ import annotations

import math

from strategies.base import StrategyDecision, _safe_float, liquidity_ok


def generate_weekly_opportunity_orders(
    rows: list[dict],
    global_cfg: dict,
    strategy_cfg: dict,
    session: str,
) -> list[StrategyDecision]:
    """Convert pre-scored weekly-opportunity rows into long-only candidates.

    This adapter intentionally does not load or promote a model artifact. The
    upstream research/serving layer must provide ``weekly_opportunity_score`` (or
    the configured score field). Keeping scoring and activation separate makes
    it impossible for merely registering this strategy to turn the challenger
    into a canonical model.
    """
    del session  # Weekly strategy is not tied to an intraday timing bucket.

    score_field = str(strategy_cfg.get("model_score_field") or "weekly_opportunity_score")
    min_score = _safe_float(strategy_cfg.get("entry", {}).get("min_opportunity_score"), 0.0)
    top_fraction = _safe_float(strategy_cfg.get("entry", {}).get("top_fraction"), 0.20)
    top_fraction = max(0.0, min(1.0, top_fraction))

    scored: list[tuple[dict, float]] = []
    for row in rows:
        raw_score = row.get(score_field)
        if raw_score in (None, ""):
            continue
        score = _safe_float(raw_score, float("-inf"))
        if not math.isfinite(score):
            continue
        scored.append((row, score))

    if not scored or top_fraction <= 0:
        return []

    scored.sort(key=lambda item: item[1], reverse=True)
    top_n = max(1, math.ceil(len(scored) * top_fraction))
    selected = [(row, score) for row, score in scored if score > min_score][:top_n]

    nav = _safe_float(global_cfg.get("portfolio", {}).get("nav_usd"), 0.0)
    max_weight = _safe_float(strategy_cfg.get("position_sizing", {}).get("max_weight_pct_nav"), 0.0)
    max_notional = _safe_float(strategy_cfg.get("position_sizing", {}).get("max_notional_usd"), 0.0)
    budget = min(nav * max_weight, max_notional)

    out: list[StrategyDecision] = []
    for row, score in selected:
        close = _safe_float(row.get("close"), 0.0)
        floor_q1 = _safe_float(row.get("floor_q1"), 0.0)
        ceiling_q1 = _safe_float(row.get("ceiling_q1"), 0.0)
        if close <= 0 or floor_q1 <= 0 or ceiling_q1 <= 0:
            continue
        if floor_q1 >= close or ceiling_q1 <= close:
            continue
        if not liquidity_ok(row, strategy_cfg):
            continue

        qty = int(max(0.0, budget / close))
        if qty <= 0:
            continue

        out.append(
            StrategyDecision(
                strategy_id="weekly_opportunity_ridge",
                symbol=str(row["symbol"]),
                side="BUY",
                score=score,
                qty=qty,
                horizon="q1",
                entry_reason=(
                    f"Weekly opportunity score {score:.4f}; positive top-"
                    f"{top_fraction:.0%} cross-sectional rank"
                ),
                exit_reason="Maximum holding horizon 10 business days; q1 floor/ceiling are risk context only",
                stop_price=floor_q1,
                take_profit_price=ceiling_q1,
                expected_return=_safe_float(row.get("expected_return_q1"), 0.0),
                expected_range=max(0.0, ceiling_q1 - floor_q1),
                timing_alignment=_safe_float(strategy_cfg.get("timing_base_score"), 0.5),
            )
        )

    return out
