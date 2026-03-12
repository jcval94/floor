from __future__ import annotations

from backtest.cost_model import CostModelConfig
from execution.order_models import MINIMUM_SCHEMAS
from execution.paper_executor import PaperExecutionConfig, PaperExecutor
from execution.reconciliation import reconcile_signals_orders_fills


def run_paper_trading(cycles: list[dict], config: dict) -> dict:
    paper_cfg = PaperExecutionConfig(**config["execution"])
    cost_cfg = CostModelConfig(**config["costs"])
    executor = PaperExecutor(
        config=paper_cfg,
        cost_config=cost_cfg,
        initial_cash=float(config["portfolio"]["initial_cash"]),
    )

    cycle_outputs = []
    all_signals: list[dict] = []
    for c in cycles:
        cycle_id = str(c["cycle_id"])
        timestamp = str(c["timestamp"])
        signals = list(c.get("signals", []))
        all_signals.extend(
            {
                "cycle_id": cycle_id,
                "strategy_id": str(s["strategy_id"]),
                "symbol": str(s["symbol"]),
            }
            for s in signals
        )

        out = executor.run_cycle(cycle_id, timestamp, signals, c.get("market_data", {}))
        cycle_outputs.append(out)

    reconciliation = reconcile_signals_orders_fills(
        signals=all_signals,
        orders=[o.to_record() for o in executor.order_book.values()],
        fills=[f.to_record() for f in executor.fills],
    )

    return {
        "config": {
            "paper_trading_enabled": paper_cfg.paper_trading_enabled,
            "live_trading_enabled": paper_cfg.live_trading_enabled,
        },
        "schemas": MINIMUM_SCHEMAS,
        "cycles": cycle_outputs,
        "orders": [o.to_record() for o in executor.order_book.values()],
        "fills": [f.to_record() for f in executor.fills],
        "trades": [t.to_record() for t in executor.trades],
        "portfolio_snapshots": [s.to_record() for s in executor.snapshots],
        "cash_ledger": list(executor.cash_ledger),
        "exposure_report": [
            {
                "cycle_id": s.cycle_id,
                "gross_exposure": s.gross_exposure,
                "net_exposure": s.net_exposure,
            }
            for s in executor.snapshots
        ],
        "reconciliation": reconciliation,
    }
