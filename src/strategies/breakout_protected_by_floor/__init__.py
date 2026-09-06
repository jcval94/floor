"""D1 breakout strategy protected by Floor/Ceiling geometry."""

from strategies.breakout_protected_by_floor.strategy import (
    STRATEGY_ID,
    generate_breakout_floor_orders,
)

generate_orders = generate_breakout_floor_orders

__all__ = ["STRATEGY_ID", "generate_breakout_floor_orders", "generate_orders"]
