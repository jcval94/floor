from __future__ import annotations

from dataclasses import replace

from strategies.base import StrategyDecision
from strategies.common import hold_decision, to_float

STRATEGY_ID = "volatility_regime_switch"

_BREAKOUT = "breakout_protected_by_floor"
_MEAN_REVERSION = "mean_reversion_floor_w1"


def _normalize_regime(row: dict, strategy_cfg: dict) -> str:
    raw = str(row.get("vol_regime") or "").strip().upper()
    if raw in {"LOW", "NORMAL", "HIGH"}:
        return raw

    score = to_float(row.get("vol_regime_score"), -1.0)
    if score < 0:
        return "UNKNOWN"
    low_max = to_float(strategy_cfg.get("low_regime_max_score"), 0.8)
    high_min = to_float(strategy_cfg.get("high_regime_min_score"), 1.2)
    if score < low_max:
        return "LOW"
    if score > high_min:
        return "HIGH"
    return "NORMAL"


def _by_symbol(decisions: list[StrategyDecision]) -> dict[str, StrategyDecision]:
    return {str(item.symbol): item for item in decisions}


def _actionable(decision: StrategyDecision | None) -> bool:
    return (
        decision is not None
        and str(decision.side).upper() in {"BUY", "SELL"}
        and int(decision.qty) > 0
    )


def route_volatility_regime_decisions(
    decisions_by_strategy: dict[str, list[StrategyDecision]],
    rows_by_symbol: dict[str, dict],
    strategy_cfg: dict,
) -> list[StrategyDecision]:
    """Route already cost-valid source decisions by point-in-time volatility regime.

    This meta-strategy never estimates alpha itself. LOW volatility delegates to
    mean reversion, HIGH volatility delegates to breakout/trend, and NORMAL
    volatility allows either source. In NORMAL, conflicting sides fail closed to
    HOLD instead of pretending the source scores are directly comparable.
    """

    breakout = _by_symbol(decisions_by_strategy.get(_BREAKOUT, []))
    mean_reversion = _by_symbol(decisions_by_strategy.get(_MEAN_REVERSION, []))

    output: list[StrategyDecision] = []
    for symbol in sorted(rows_by_symbol):
        row = rows_by_symbol[symbol]
        regime = _normalize_regime(row, strategy_cfg)
        breakout_decision = breakout.get(symbol)
        mean_decision = mean_reversion.get(symbol)

        selected: StrategyDecision | None = None
        source = ""

        if regime == "LOW":
            if _actionable(mean_decision):
                selected = mean_decision
                source = _MEAN_REVERSION
        elif regime == "HIGH":
            if _actionable(breakout_decision):
                selected = breakout_decision
                source = _BREAKOUT
        elif regime == "NORMAL":
            candidates = [
                item
                for item in (breakout_decision, mean_decision)
                if _actionable(item)
            ]
            if len(candidates) == 1:
                selected = candidates[0]
                source = str(selected.strategy_id)
            elif len(candidates) >= 2:
                sides = {str(item.side).upper() for item in candidates}
                if len(sides) == 1:
                    selected = max(
                        candidates,
                        key=lambda item: (
                            float(item.net_alpha_pct),
                            float(item.score),
                        ),
                    )
                    source = str(selected.strategy_id)

        if selected is None:
            reason = (
                f"HOLD: volatility regime={regime}; "
                "no compatible cost-valid source decision"
            )
            if (
                regime == "NORMAL"
                and _actionable(breakout_decision)
                and _actionable(mean_decision)
                and str(breakout_decision.side).upper()
                != str(mean_decision.side).upper()
            ):
                reason = (
                    "HOLD: NORMAL volatility regime with conflicting breakout "
                    "and mean-reversion directions"
                )
            output.append(
                hold_decision(
                    STRATEGY_ID,
                    row,
                    "d1",
                    reason,
                )
            )
            continue

        output.append(
            replace(
                selected,
                strategy_id=STRATEGY_ID,
                entry_reason=(
                    f"{selected.side}: volatility regime={regime} routes "
                    f"source={source}; {selected.entry_reason}"
                ),
                exit_reason=(
                    f"Source strategy exit semantics preserved from {source}. "
                    f"{selected.exit_reason}"
                ),
                alpha_source=(
                    f"routed:{source}:{selected.alpha_source or 'source_alpha'}"
                ),
            )
        )

    return output
