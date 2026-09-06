from __future__ import annotations

from collections import defaultdict

from portfolio.risk_allocator import PortfolioState, cap_order_quantity
from strategies.base import StrategyDecision, build_order_payload


def _allowed_by_cooldown(
    decision: StrategyDecision,
    cooldown_state: dict[str, int],
    current_cycle: int,
    cooldown_cycles: int,
) -> bool:
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
    portfolio_state: dict | None = None,
) -> dict:
    """Resolve strategy collisions and apply account-level capital constraints.

    Strategies are allowed to propose quantities, but the allocator is the
    final authority on executable size. It never increases a strategy's
    proposal; it can only accept, reduce or reject it based on portfolio risk.
    """

    cooldown_state = cooldown_state or {}
    priorities = {k: int(v["priority"]) for k, v in config["strategies"].items()}
    state = PortfolioState.from_mapping(portfolio_state, config)

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

    allocated_notional = 0.0
    allocated_risk = 0.0
    allocated_buy_notional = 0.0
    allocated_sector: dict[str, float] = defaultdict(float)

    for d in ordered:
        if len(accepted) >= max_orders_cycle:
            blocked.append(
                {
                    "symbol": d.symbol,
                    "strategy": d.strategy_id,
                    "reason": "Max rotation per cycle reached",
                }
            )
            continue

        symbol_row = rows_by_symbol.get(d.symbol, {})
        sector = str(symbol_row.get("sector", "UNKNOWN"))

        if per_ticker_count[d.symbol] >= max_per_ticker:
            blocked.append(
                {
                    "symbol": d.symbol,
                    "strategy": d.strategy_id,
                    "reason": "Ticker limit exceeded",
                }
            )
            continue
        if sector_count[sector] >= max_per_sector:
            blocked.append(
                {
                    "symbol": d.symbol,
                    "strategy": d.strategy_id,
                    "reason": "Sector limit exceeded",
                }
            )
            continue

        strategy_cfg = config["strategies"][d.strategy_id]
        if not _allowed_by_cooldown(
            d,
            cooldown_state,
            current_cycle,
            int(strategy_cfg["cooldown_cycles"]),
        ):
            blocked.append(
                {
                    "symbol": d.symbol,
                    "strategy": d.strategy_id,
                    "reason": "Cooldown active",
                }
            )
            continue

        # Explicit collision rule: if the same ticker has multiple strategies,
        # the higher-priority accepted strategy owns the symbol this cycle.
        if d.symbol in seen_symbol:
            blocked.append(
                {
                    "symbol": d.symbol,
                    "strategy": d.strategy_id,
                    "reason": "Strategy collision lost by priority",
                }
            )
            continue

        allocation = cap_order_quantity(
            proposed_qty=int(d.qty),
            side=d.side,
            entry_price=float(symbol_row.get("close", 0.0) or 0.0),
            stop_price=float(d.stop_price or 0.0),
            sector=sector,
            strategy_cfg=strategy_cfg,
            global_cfg=config,
            state=state,
            allocated_notional=allocated_notional,
            allocated_risk=allocated_risk,
            allocated_sector_notional=allocated_sector[sector],
            allocated_buy_notional=allocated_buy_notional,
        )
        if allocation.quantity <= 0:
            blocked.append(
                {
                    "symbol": d.symbol,
                    "strategy": d.strategy_id,
                    "reason": allocation.blocked_reason
                    or f"Portfolio allocation blocked by {allocation.binding_limit}",
                    "binding_limit": allocation.binding_limit,
                }
            )
            continue

        payload = build_order_payload(d, strategy_cfg, config)
        payload["quantity"] = allocation.quantity
        payload["sector"] = sector
        payload["effective_priority"] = int(
            priorities.get(d.strategy_id, 999) + int(d.priority_adjustment or 0)
        )
        payload["allocation"] = allocation.as_dict()
        payload["quantity_reduced_by_portfolio"] = allocation.quantity < int(d.qty)
        accepted.append(payload)

        seen_symbol.add(d.symbol)
        per_ticker_count[d.symbol] += 1
        sector_count[sector] += 1
        allocated_notional += allocation.notional_usd
        allocated_risk += allocation.risk_at_stop_usd
        allocated_sector[sector] += allocation.notional_usd
        if str(d.side).upper() == "BUY":
            allocated_buy_notional += allocation.notional_usd

    equity = max(state.equity, 1e-12)
    summary = {
        "equity_usd": round(state.equity, 6),
        "cash_usd": round(state.cash, 6),
        "starting_gross_exposure_usd": round(state.gross_exposure, 6),
        "starting_open_risk_usd": round(state.open_risk, 6),
        "allocated_notional_usd": round(allocated_notional, 6),
        "allocated_risk_usd": round(allocated_risk, 6),
        "gross_exposure_after_pct_nav": round(
            (state.gross_exposure + allocated_notional) / equity,
            8,
        ),
        "portfolio_heat_after_pct_nav": round(
            (state.open_risk + allocated_risk) / equity,
            8,
        ),
    }
    return {
        "orders": accepted,
        "blocked_collisions": blocked,
        "portfolio_summary": summary,
    }
