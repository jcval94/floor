"""W1 mean-reversion strategy around Floor/Ceiling anchors."""

from strategies.mean_reversion_floor_w1.strategy import (
    STRATEGY_ID,
    generate_mean_reversion_orders,
)

generate_orders = generate_mean_reversion_orders

__all__ = ["STRATEGY_ID", "generate_mean_reversion_orders", "generate_orders"]
