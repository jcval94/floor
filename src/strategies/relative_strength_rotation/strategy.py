from __future__ import annotations

from dataclasses import replace
from math import ceil

from strategies.base import StrategyDecision
from strategies.common import hold_decision, to_float

STRATEGY_ID = "relative_strength_rotation"


def _csv(value: object) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _decision_map(
    decisions_by_strategy: dict[str, list[StrategyDecision]],
) -> dict[str, dict[str, StrategyDecision]]:
    return {
        strategy_id: {str(item.symbol): item for item in decisions}
        for strategy_id, decisions in decisions_by_strategy.items()
    }


def _source_decision(
    symbol: str,
    decision_maps: dict[str, dict[str, StrategyDecision]],
    source_priority: list[str],
) -> StrategyDecision | None:
    for source in source_priority:
        decision = decision_maps.get(source, {}).get(symbol)
        if (
            decision is not None
            and str(decision.side).upper() == "BUY"
            and int(decision.qty) > 0
            and float(decision.net_alpha_pct) > 0
        ):
            return decision
    return None


def _relative_strength_score(
    row: dict,
    strategy_cfg: dict,
) -> tuple[float, int] | None:
    weights = {
        "rel_strength_4w": max(0.0, to_float(strategy_cfg.get("weight_4w"), 0.50)),
        "rel_strength_8w": max(0.0, to_float(strategy_cfg.get("weight_8w"), 0.30)),
        "rel_strength_13w": max(0.0, to_float(strategy_cfg.get("weight_13w"), 0.20)),
    }
    observed: list[tuple[float, float]] = []
    for field, weight in weights.items():
        raw = row.get(field)
        if raw is None or weight <= 0:
            continue
        observed.append((to_float(raw), weight))

    min_horizons = max(1, int(to_float(strategy_cfg.get("min_rs_horizons"), 2)))
    if len(observed) < min_horizons:
        return None

    denominator = sum(weight for _, weight in observed)
    if denominator <= 0:
        return None
    composite = sum(value * weight for value, weight in observed) / denominator

    beta = abs(to_float(row.get("beta_20"), 1.0))
    max_abs_beta = max(0.0, to_float(strategy_cfg.get("max_abs_beta"), 1.80))
    if max_abs_beta > 0 and beta > max_abs_beta:
        return None

    beta_penalty = max(0.0, to_float(strategy_cfg.get("beta_penalty_weight"), 0.50))
    adjusted = composite / (1.0 + beta_penalty * max(0.0, beta - 1.0))
    return adjusted, len(observed)


def build_relative_strength_rotation(
    decisions_by_strategy: dict[str, list[StrategyDecision]],
    rows_by_symbol: dict[str, dict],
    strategy_cfg: dict,
    *,
    held_symbols: set[str] | None = None,
) -> list[StrategyDecision]:
    """Rotate only among names that already have cost-valid long alpha.

    Relative strength is a cross-sectional ranking feature, never a substitute
    for expected return. The selected source decision keeps its original alpha,
    stop and take-profit semantics. Hysteresis is supported to reduce turnover.
    """

    held_symbols = held_symbols or set()
    source_priority = _csv(
        strategy_cfg.get(
            "source_priority",
            (
                "weekly_opportunity_ridge,"
                "breakout_protected_by_floor,"
                "mean_reversion_floor_w1"
            ),
        )
    )
    decision_maps = _decision_map(decisions_by_strategy)

    min_composite = to_float(strategy_cfg.get("min_composite_rs"), 0.0)
    candidates: list[dict] = []
    for symbol, row in rows_by_symbol.items():
        source = _source_decision(symbol, decision_maps, source_priority)
        if source is None:
            continue
        scored = _relative_strength_score(row, strategy_cfg)
        if scored is None:
            continue
        adjusted_rs, horizons = scored
        if adjusted_rs < min_composite:
            continue
        candidates.append(
            {
                "symbol": symbol,
                "row": row,
                "decision": source,
                "source": str(source.strategy_id),
                "adjusted_rs": adjusted_rs,
                "horizons": horizons,
                "sector": str(row.get("sector") or "UNKNOWN"),
            }
        )

    candidates.sort(
        key=lambda item: (
            float(item["adjusted_rs"]),
            float(item["decision"].net_alpha_pct),
            str(item["symbol"]),
        ),
        reverse=True,
    )

    n = len(candidates)
    top_fraction = min(
        1.0,
        max(0.0, to_float(strategy_cfg.get("top_fraction"), 0.25)),
    )
    retain_fraction = min(
        1.0,
        max(
            top_fraction,
            to_float(strategy_cfg.get("retain_top_fraction"), 0.40),
        ),
    )
    max_positions = max(1, int(to_float(strategy_cfg.get("max_positions"), 5)))
    max_per_sector = max(1, int(to_float(strategy_cfg.get("max_per_sector"), 2)))

    entry_cut = min(n, max_positions, max(1, ceil(n * top_fraction))) if n else 0
    retain_cut = min(n, max_positions, max(1, ceil(n * retain_fraction))) if n else 0

    desired_symbols = {
        str(item["symbol"])
        for item in candidates[:entry_cut]
    }
    desired_symbols.update(
        str(item["symbol"])
        for item in candidates[:retain_cut]
        if str(item["symbol"]) in held_symbols
    )

    selected_symbols: set[str] = set()
    sector_count: dict[str, int] = {}
    for item in candidates:
        symbol = str(item["symbol"])
        if symbol not in desired_symbols:
            continue
        if len(selected_symbols) >= max_positions:
            break
        sector = str(item["sector"])
        if sector_count.get(sector, 0) >= max_per_sector:
            continue
        selected_symbols.add(symbol)
        sector_count[sector] = sector_count.get(sector, 0) + 1

    candidate_by_symbol = {
        str(item["symbol"]): item
        for item in candidates
    }
    output: list[StrategyDecision] = []
    for symbol in sorted(rows_by_symbol):
        row = rows_by_symbol[symbol]
        item = candidate_by_symbol.get(symbol)
        if item is None or symbol not in selected_symbols:
            output.append(
                hold_decision(
                    STRATEGY_ID,
                    row,
                    "q1",
                    (
                        "HOLD: no cost-valid long source in the "
                        "relative-strength rotation set"
                    ),
                )
            )
            continue

        source: StrategyDecision = item["decision"]
        retained = symbol in held_symbols and symbol not in {
            str(entry["symbol"]) for entry in candidates[:entry_cut]
        }
        output.append(
            replace(
                source,
                strategy_id=STRATEGY_ID,
                score=float(item["adjusted_rs"]),
                entry_reason=(
                    f"BUY: relative-strength rotation score="
                    f"{float(item['adjusted_rs']):.4f}, "
                    f"horizons={int(item['horizons'])}, "
                    f"source={item['source']}; {source.entry_reason}"
                    + (" · retained_by_hysteresis" if retained else "")
                ),
                exit_reason=(
                    "Leaves rotation set or source exit fires. "
                    f"Source semantics preserved from {item['source']}."
                ),
                alpha_source=(
                    f"rotation_preserves:{item['source']}:"
                    f"{source.alpha_source or 'source_alpha'}"
                ),
            )
        )

    return output
