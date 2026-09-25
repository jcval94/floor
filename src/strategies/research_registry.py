"""Isolated registry for research-only challengers.

These strategies are intentionally excluded from the active strategy registry and
Strategy League frozen contracts until they earn a separate OOS evidence epoch.
"""

from __future__ import annotations

from typing import Callable

from strategies.base import StrategyDecision
from strategies.floor_ceiling_reclaim import (
    STRATEGY_ID as FLOOR_CEILING_RECLAIM_ID,
    generate_orders as generate_floor_ceiling_reclaim_orders,
)
from strategies.gap_to_floor_ceiling import (
    STRATEGY_ID as GAP_TO_FLOOR_CEILING_ID,
    generate_orders as generate_gap_to_floor_ceiling_orders,
)
from strategies.relative_strength_rotation import (
    STRATEGY_ID as RELATIVE_STRENGTH_ROTATION_ID,
    build_relative_strength_rotation,
)
from strategies.volatility_regime_switch import (
    STRATEGY_ID as VOLATILITY_REGIME_SWITCH_ID,
    route_volatility_regime_decisions,
)

ResearchStrategyGenerator = Callable[
    [list[dict], dict, dict, str],
    list[StrategyDecision],
]

RESEARCH_ONLY_STRATEGY_GENERATORS: dict[str, ResearchStrategyGenerator] = {
    FLOOR_CEILING_RECLAIM_ID: generate_floor_ceiling_reclaim_orders,
    GAP_TO_FLOOR_CEILING_ID: generate_gap_to_floor_ceiling_orders,
}

RESEARCH_ONLY_META_STRATEGY_GENERATORS: dict[str, Callable[..., list[StrategyDecision]]] = {
    VOLATILITY_REGIME_SWITCH_ID: route_volatility_regime_decisions,
    RELATIVE_STRENGTH_ROTATION_ID: build_relative_strength_rotation,
}

RESEARCH_ONLY_STRATEGY_IDS = tuple(
    [
        *RESEARCH_ONLY_STRATEGY_GENERATORS,
        *RESEARCH_ONLY_META_STRATEGY_GENERATORS,
    ]
)
