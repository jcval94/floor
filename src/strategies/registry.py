from __future__ import annotations

from typing import Callable

from strategies.base import StrategyDecision
from strategies.breakout_protected_by_floor import (
    STRATEGY_ID as BREAKOUT_ID,
    generate_orders as generate_breakout_orders,
)
from strategies.cross_horizon_asymmetry import (
    STRATEGY_ID as CROSS_HORIZON_ID,
    generate_orders as generate_cross_horizon_orders,
)
from strategies.mean_reversion_floor_w1 import (
    STRATEGY_ID as MEAN_REVERSION_ID,
    generate_orders as generate_mean_reversion_orders,
)
from strategies.weekly_opportunity_ridge import (
    STRATEGY_ID as WEEKLY_ID,
    generate_orders as generate_weekly_orders,
)

StrategyGenerator = Callable[[list[dict], dict, dict, str], list[StrategyDecision]]

STRATEGY_GENERATORS: dict[str, StrategyGenerator] = {
    WEEKLY_ID: generate_weekly_orders,
    BREAKOUT_ID: generate_breakout_orders,
    MEAN_REVERSION_ID: generate_mean_reversion_orders,
    CROSS_HORIZON_ID: generate_cross_horizon_orders,
}

ACTIVE_STRATEGY_IDS = tuple(STRATEGY_GENERATORS)


def validate_registry() -> None:
    """Fail fast if package IDs ever drift from the central registry contract."""
    ids = [WEEKLY_ID, BREAKOUT_ID, MEAN_REVERSION_ID, CROSS_HORIZON_ID]
    if len(ids) != len(set(ids)):
        raise RuntimeError(f"Duplicate strategy ids in registry: {ids}")
    if set(ids) != set(STRATEGY_GENERATORS):
        raise RuntimeError("Strategy registry keys do not match package STRATEGY_ID values")


validate_registry()
