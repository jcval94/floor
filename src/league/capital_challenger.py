from __future__ import annotations

from collections import defaultdict
from typing import Any

from strategies.base import StrategyDecision
from strategies.common import round_trip_cost_bps


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _ranked_buys(decisions: list[StrategyDecision]) -> list[tuple[StrategyDecision, float]]:
    buys = [
        item
        for item in decisions
        if str(item.side).upper() == "BUY" and int(item.qty) > 0
    ]
    buys.sort(key=lambda item: float(item.score))
    if not buys:
        return []
    if len(buys) == 1:
        return [(buys[0], 1.0)]
    denominator = max(len(buys) - 1, 1)
    return [
        (item, 0.50 + 0.50 * (index / denominator))
        for index, item in enumerate(buys)
    ]


def build_capital_challenger_targets(
    decisions_by_strategy: dict[str, list[StrategyDecision]],
    rows_by_symbol: dict[str, dict],
    strategies_cfg: dict,
    challenger_cfg: dict,
) -> dict[str, dict]:
    """Create long-only targets from the best existing strategy signals.

    Scores are percentile-ranked *within* each source strategy before they are
    combined. That deliberately avoids pretending that raw scores from four
    different strategy families are calibrated on the same numeric scale.

    The resulting weights are stop-risk sized and then capped by portfolio
    heat, gross exposure, sector exposure and per-position limits. Consensus
    improves ranking, but it never raises the risk budget above the configured
    base risk per position.
    """

    source_weights = challenger_cfg.get("source_weights", {})
    quality_floor = max(0.0, min(1.0, _float(challenger_cfg.get("quality_floor"), 0.55)))
    consensus_bonus = max(0.0, _float(challenger_cfg.get("consensus_bonus"), 0.15))
    base_risk_pct = max(0.0, _float(challenger_cfg.get("risk_budget_pct_nav"), 0.0075))
    min_risk_scale = max(0.0, min(1.0, _float(challenger_cfg.get("min_risk_scale"), 0.65)))
    max_position = max(0.0, min(1.0, _float(challenger_cfg.get("max_position_pct_nav"), 0.20)))
    max_gross = max(0.0, min(1.0, _float(challenger_cfg.get("max_gross_exposure_pct_nav"), 0.90)))
    max_heat = max(0.0, _float(challenger_cfg.get("max_portfolio_heat_pct_nav"), 0.04))
    max_sector = max(0.0, min(1.0, _float(challenger_cfg.get("max_sector_exposure_pct_nav"), 0.35)))
    min_weight = max(0.0, min(1.0, _float(challenger_cfg.get("min_position_weight"), 0.02)))

    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for strategy_id, source_decisions in decisions_by_strategy.items():
        source_weight = max(0.0, _float(source_weights.get(strategy_id), 1.0))
        for ranked_decision, percentile_quality in _ranked_buys(source_decisions):
            symbol = str(ranked_decision.symbol)
            row = rows_by_symbol.get(symbol, {})
            close = _float(row.get("close"))
            stop = _float(ranked_decision.stop_price)
            if close <= 0 or stop <= 0 or stop >= close:
                continue
            source_quality = min(1.0, percentile_quality * source_weight)
            if source_quality < quality_floor:
                continue
            by_symbol[symbol].append(
                {
                    "decision": ranked_decision,
                    "strategy_id": strategy_id,
                    "source_quality": source_quality,
                    "close": close,
                    "sector": str(row.get("sector", "UNKNOWN")),
                }
            )

    candidates: list[dict[str, Any]] = []
    cost_pct = round_trip_cost_bps(strategies_cfg) / 10000.0
    for symbol, signals in by_symbol.items():
        sources = sorted({str(signal["strategy_id"]) for signal in signals})
        best = max(
            signals,
            key=lambda signal: (
                float(signal["source_quality"]),
                float(signal["decision"].score),
            ),
        )
        selected_decision: StrategyDecision = best["decision"]
        close = float(best["close"])
        stop = float(selected_decision.stop_price)
        stop_risk_pct = abs(close - stop) / close + cost_pct
        if stop_risk_pct <= 0:
            continue

        base_quality = float(best["source_quality"])
        consensus_count = len(sources)
        allocation_score = base_quality * (
            1.0 + consensus_bonus * max(consensus_count - 1, 0)
        )
        risk_scale = min_risk_scale + (1.0 - min_risk_scale) * min(base_quality, 1.0)
        risk_budget_pct = base_risk_pct * risk_scale
        weight_by_stop_risk = risk_budget_pct / stop_risk_pct
        proposed_weight = min(max_position, weight_by_stop_risk)
        if proposed_weight < min_weight:
            continue

        candidates.append(
            {
                "symbol": symbol,
                "decision": selected_decision,
                "sector": str(best["sector"]),
                "sources": sources,
                "consensus_count": consensus_count,
                "allocation_score": allocation_score,
                "stop_risk_pct": stop_risk_pct,
                "risk_budget_pct": risk_budget_pct,
                "proposed_weight": proposed_weight,
            }
        )

    candidates.sort(
        key=lambda item: (
            float(item["allocation_score"]),
            int(item["consensus_count"]),
        ),
        reverse=True,
    )

    targets: dict[str, dict] = {}
    gross_used = 0.0
    heat_used = 0.0
    sector_used: dict[str, float] = defaultdict(float)

    for candidate in candidates:
        if gross_used >= max_gross - 1e-12 or heat_used >= max_heat - 1e-12:
            break
        risk_pct = float(candidate["stop_risk_pct"])
        sector = str(candidate["sector"])
        remaining_gross = max(0.0, max_gross - gross_used)
        remaining_heat_weight = max(0.0, max_heat - heat_used) / max(risk_pct, 1e-12)
        remaining_sector = max(0.0, max_sector - sector_used[sector])
        weight = min(
            float(candidate["proposed_weight"]),
            remaining_gross,
            remaining_heat_weight,
            remaining_sector,
        )
        if weight < min_weight:
            continue

        target_decision: StrategyDecision = candidate["decision"]
        targets[str(candidate["symbol"])] = {
            "weight": weight,
            "stop_price": float(target_decision.stop_price or 0.0),
            "take_profit_price": float(target_decision.take_profit_price or 0.0),
            "score": float(candidate["allocation_score"]),
            "source_strategy": str(target_decision.strategy_id),
            "source_strategies": list(candidate["sources"]),
            "consensus_count": int(candidate["consensus_count"]),
            "stop_risk_pct": risk_pct,
            "risk_budget_pct_nav": float(candidate["risk_budget_pct"]),
        }
        gross_used += weight
        heat_used += weight * risk_pct
        sector_used[sector] += weight

    return targets
