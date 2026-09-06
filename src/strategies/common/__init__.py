"""Shared mechanics used by all strategy packages.

Only cross-strategy primitives belong here. Directional rules must live inside
one strategy package so strategies cannot silently overlap their logic.
"""

from strategies.common.mechanics import (
    apply_m3_context,
    geometry,
    hold_decision,
    liquidity_ok,
    net_edge,
    platform_fee_bps_per_side,
    risk_sized_qty,
    round_trip_cost_bps,
    to_float,
)

__all__ = [
    "apply_m3_context",
    "geometry",
    "hold_decision",
    "liquidity_ok",
    "net_edge",
    "platform_fee_bps_per_side",
    "risk_sized_qty",
    "round_trip_cost_bps",
    "to_float",
]
