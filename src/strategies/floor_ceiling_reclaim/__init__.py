"""Intraday D1 Floor/Ceiling reclaim research challenger."""

from strategies.floor_ceiling_reclaim.strategy import (
    STRATEGY_ID,
    generate_floor_ceiling_reclaim_orders,
)

generate_orders = generate_floor_ceiling_reclaim_orders

__all__ = ["STRATEGY_ID", "generate_floor_ceiling_reclaim_orders", "generate_orders"]
