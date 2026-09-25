"""Opening Gap-to-Floor / Gap-to-Ceiling research challenger."""

from strategies.gap_to_floor_ceiling.strategy import (
    STRATEGY_ID,
    generate_gap_to_floor_ceiling_orders,
)

generate_orders = generate_gap_to_floor_ceiling_orders

__all__ = ["STRATEGY_ID", "generate_gap_to_floor_ceiling_orders", "generate_orders"]
