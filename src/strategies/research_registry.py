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

ResearchStrategyGenerator = Callable[
    [list[dict], dict, dict, str],
    list[StrategyDecision],
]

RESEARCH_ONLY_STRATEGY_GENERATORS: dict[str, ResearchStrategyGenerator] = {
    FLOOR_CEILING_RECLAIM_ID: generate_floor_ceiling_reclaim_orders,
}

RESEARCH_ONLY_STRATEGY_IDS = tuple(RESEARCH_ONLY_STRATEGY_GENERATORS)
