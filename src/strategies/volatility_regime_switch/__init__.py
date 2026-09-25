"""Volatility-regime meta-strategy for research routing."""

from strategies.volatility_regime_switch.strategy import (
    STRATEGY_ID,
    route_volatility_regime_decisions,
)

__all__ = ["STRATEGY_ID", "route_volatility_regime_decisions"]
