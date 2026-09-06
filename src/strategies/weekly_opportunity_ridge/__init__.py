"""Weekly Ridge opportunity strategy."""

from strategies.weekly_opportunity_ridge.strategy import (
    STRATEGY_ID,
    generate_weekly_opportunity_orders,
)

generate_orders = generate_weekly_opportunity_orders

__all__ = ["STRATEGY_ID", "generate_weekly_opportunity_orders", "generate_orders"]
