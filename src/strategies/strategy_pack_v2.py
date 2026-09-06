"""Backward-compatible facade for Strategy Pack v2.

No strategy logic belongs in this module. Implementations live in one package
per strategy under ``strategies/<strategy_id>/``.
"""

from strategies.breakout_protected_by_floor import generate_breakout_floor_orders
from strategies.common import platform_fee_bps_per_side, round_trip_cost_bps
from strategies.cross_horizon_asymmetry import generate_cross_horizon_orders
from strategies.mean_reversion_floor_w1 import generate_mean_reversion_orders
from strategies.weekly_opportunity_ridge import generate_weekly_opportunity_orders

__all__ = [
    "generate_breakout_floor_orders",
    "generate_cross_horizon_orders",
    "generate_mean_reversion_orders",
    "generate_weekly_opportunity_orders",
    "platform_fee_bps_per_side",
    "round_trip_cost_bps",
]
