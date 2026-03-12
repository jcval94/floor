from __future__ import annotations

from collections import defaultdict

from strategies.base import StrategyDecision, build_order_payload


def _allowed_by_cooldown(decision: StrategyDecision, cooldown_state: dict[str, int], current_cycle: int, cooldown_cycles: int) -> bool:
    last_cycle = cooldown_state.get(f"{decision.strategy_id}:{decision.symbol}")
    if last_cycle is None:
        return True
    return (current_cycle - last_cycle) >= cooldown_cycles


def allocate_orders(
    candidates: list[StrategyDecision],
    rows_by_symbol: dict[str, dict],
    config: dict,
    cooldown_state: dict[str, int] | None = None,
    current_cycle: int = 0,
) -> dict:
    cooldown_state = cooldown_state or {}
    priorities = {k: int(v["priority"]) for k, v in config["strategies"].items()}

    def _effective_priority(decision: StrategyDecision) -> int:
        base = priorities.get(decision.strategy_id, 999)
        return int(base + int(decision.priority_adjustment or 0))

    ordered = sorted(
        candidates,
        key=lambda d: (_effective_priority(d), -d.score),
    )

    accepted: list[dict] = []
    blocked: list[dict] = []
    seen_symbol: set[str] = set()
    sector_count: dict[str, int] = defaultdict(int)

    max_orders_cycle = int(config["portfolio"]["max_orders_per_cycle"])
    max_per_ticker = int(config["portfolio"]["max_orders_per_ticker"])
    max_per_sector = int(config["portfolio"]["max_orders_per_sector"])
    per_ticker_count: dict[str, int] = defaultdict(int)

    for d in ordered:
        if len(accepted) >= max_orders_cycle:
            blocked.append({"symbol": d.symbol, "strategy": d.strategy_id, "reason": "Max rotation per cycle reached"})
            continue

        symbol_row = rows_by_symbol.get(d.symbol, {})
        sector = str(symbol_row.get("sector", "UNKNOWN"))

        if per_ticker_count[d.symbol] >= max_per_ticker:
            blocked.append({"symbol": d.symbol, "strategy": d.strategy_id, "reason": "Ticker limit exceeded"})
            continue
        if sector_count[sector] >= max_per_sector:
            blocked.append({"symbol": d.symbol, "strategy": d.strategy_id, "reason": "Sector limit exceeded"})
            continue

        strategy_cfg = config["strategies"][d.strategy_id]
        if not _allowed_by_cooldown(d, cooldown_state, current_cycle, int(strategy_cfg["cooldown_cycles"])):
            blocked.append({"symbol": d.symbol, "strategy": d.strategy_id, "reason": "Cooldown active"})
            continue

        # Explicit collision rule: if same ticker has multiple strategies, keep higher-priority first accepted.
        if d.symbol in seen_symbol:
            blocked.append({"symbol": d.symbol, "strategy": d.strategy_id, "reason": "Strategy collision lost by priority"})
            continue

        payload = build_order_payload(d, strategy_cfg, config)
        payload["sector"] = sector
        payload["effective_priority"] = int(priorities.get(d.strategy_id, 999) + int(d.priority_adjustment or 0))
        accepted.append(payload)
        seen_symbol.add(d.symbol)
        per_ticker_count[d.symbol] += 1
        sector_count[sector] += 1

    return {"orders": accepted, "blocked_collisions": blocked}
