from __future__ import annotations


def reconcile_signals_orders_fills(
    signals: list[dict],
    orders: list[dict],
    fills: list[dict],
) -> dict:
    signal_keys = {(s["cycle_id"], s["strategy_id"], s["symbol"]) for s in signals}
    order_keys = {(o["cycle_id"], o["strategy_id"], o["symbol"]) for o in orders}

    fills_by_order: dict[str, int] = {}
    for f in fills:
        fills_by_order[f["order_id"]] = fills_by_order.get(f["order_id"], 0) + int(f["quantity"])

    orphan_orders = sorted(order_keys - signal_keys)
    missing_orders = sorted(signal_keys - order_keys)

    inconsistent_orders: list[str] = []
    for o in orders:
        if int(o["filled_quantity"]) != fills_by_order.get(o["order_id"], 0):
            inconsistent_orders.append(o["order_id"])

    return {
        "signal_count": len(signals),
        "order_count": len(orders),
        "fill_count": len(fills),
        "missing_orders_from_signals": missing_orders,
        "orphan_orders_without_signal": orphan_orders,
        "orders_with_fill_mismatch": sorted(inconsistent_orders),
        "is_clean": not (missing_orders or orphan_orders or inconsistent_orders),
    }
