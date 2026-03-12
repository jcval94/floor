from __future__ import annotations

import pytest

from execution.order_models import MINIMUM_SCHEMAS
from execution.run_paper_trade import run_paper_trading


def _config() -> dict:
    return {
        "execution": {
            "paper_trading_enabled": True,
            "live_trading_enabled": False,
            "max_participation_rate": 0.5,
            "price_reference": "ohlc4",
        },
        "costs": {
            "commission_bps": 1.0,
            "slippage_bps": 1.0,
            "sell_fee_bps": 2.0,
            "min_commission": 0.0,
        },
        "portfolio": {
            "initial_cash": 100_000.0,
        },
    }


def _cycles() -> list[dict]:
    return [
        {
            "cycle_id": "c1",
            "timestamp": "2026-03-12T10:00:00Z",
            "signals": [
                {"strategy_id": "s1", "symbol": "AAA", "side": "BUY", "quantity": 100},
                {"strategy_id": "s2", "symbol": "BBB", "side": "BUY", "quantity": 50},
            ],
            "market_data": {
                "AAA": {"open": 100, "high": 102, "low": 99, "close": 101, "volume": 1_000},
                "BBB": {"open": 50, "high": 51, "low": 49, "close": 50, "volume": 1_000},
            },
        },
        {
            "cycle_id": "c2",
            "timestamp": "2026-03-12T11:00:00Z",
            "signals": [
                {"strategy_id": "s1", "symbol": "AAA", "side": "SELL", "quantity": 40},
            ],
            "market_data": {
                "AAA": {"open": 102, "high": 103, "low": 100, "close": 102, "volume": 1_000},
                "BBB": {"open": 50, "high": 50, "low": 48, "close": 49, "volume": 1_000},
            },
        },
    ]


def test_paper_trading_end_to_end_and_schemas() -> None:
    result = run_paper_trading(_cycles(), _config())

    assert result["config"]["paper_trading_enabled"] is True
    assert result["config"]["live_trading_enabled"] is False

    assert set(MINIMUM_SCHEMAS) == {"orders", "fills", "trades", "portfolio_snapshots"}
    assert result["schemas"]["orders"]["status"]

    assert len(result["orders"]) == 3
    assert len(result["fills"]) == 3
    assert len(result["trades"]) == 3
    assert len(result["portfolio_snapshots"]) == 2
    assert len(result["cash_ledger"]) == 3

    snap = result["portfolio_snapshots"][-1]
    assert "realized_pnl" in snap
    assert "unrealized_pnl" in snap
    assert "gross_exposure" in snap
    assert "net_exposure" in snap

    assert result["reconciliation"]["is_clean"] is True


def test_double_execution_same_cycle_protection() -> None:
    cfg = _config()
    from backtest.cost_model import CostModelConfig
    from execution.paper_executor import PaperExecutionConfig, PaperExecutor

    executor = PaperExecutor(
        config=PaperExecutionConfig(**cfg["execution"]),
        cost_config=CostModelConfig(**cfg["costs"]),
        initial_cash=cfg["portfolio"]["initial_cash"],
    )

    cycle = _cycles()[0]
    executor.run_cycle(cycle["cycle_id"], cycle["timestamp"], cycle["signals"], cycle["market_data"])
    with pytest.raises(ValueError, match="already executed"):
        executor.run_cycle(cycle["cycle_id"], cycle["timestamp"], cycle["signals"], cycle["market_data"])
