"""Shared mechanics used by all strategy packages.

Only cross-strategy primitives belong here. Directional rules must live inside
one strategy package so strategies cannot silently overlap their logic.
"""

from strategies.common.mechanics import (
    alpha_after_costs,
    alpha_hurdle,
    alpha_hurdle_from_net,
    apply_m3_context,
    geometry,
    hold_decision,
    liquidity_ok,
    net_edge,
    payoff_room_clears_cost,
    platform_fee_bps_per_side,
    risk_sized_qty,
    round_trip_cost_bps,
    round_trip_cost_pct,
    to_float,
)

__all__ = [
    "alpha_after_costs",
    "alpha_hurdle",
    "alpha_hurdle_from_net",
    "apply_m3_context",
    "geometry",
    "hold_decision",
    "liquidity_ok",
    "net_edge",
    "payoff_room_clears_cost",
    "platform_fee_bps_per_side",
    "risk_sized_qty",
    "round_trip_cost_bps",
    "round_trip_cost_pct",
    "to_float",
]
