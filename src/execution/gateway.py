from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

from backtest.cost_model import CostModelConfig
from contracts.trading import paper_cost_contract
from execution.paper_executor import PaperExecutionConfig, PaperExecutor
from execution.risk_gateway import RiskApprovalResult, RiskPolicy, approve_signal_batch, load_risk_policy


class PaperExecutionGateway:
    """Single audited PAPER path: signal -> risk approval -> executor.

    The gateway owns one PaperExecutor so portfolio state is preserved across every
    cycle executed by this process. LIVE is intentionally absent from this adapter.
    Callers that need cross-process persistence must restore state explicitly before
    enabling scheduled execution; canonical intraday remains signal-only for now.
    """

    def __init__(
        self,
        *,
        policy: RiskPolicy,
        cost_config: CostModelConfig,
        max_participation_rate: float = 0.1,
    ) -> None:
        self.policy = policy
        self.executor = PaperExecutor(
            config=PaperExecutionConfig(
                paper_trading_enabled=True,
                live_trading_enabled=False,
                max_participation_rate=max_participation_rate,
                price_reference="ohlc4",
            ),
            cost_config=cost_config,
            initial_cash=policy.nav_usd,
        )
        self._session_key: str | None = None
        self._session_realized_pnl_start = 0.0
        self._intraday_peak_equity_usd = policy.nav_usd

    def run_cycle(
        self,
        *,
        cycle_id: str,
        timestamp: str,
        signals: list[Any],
        market_rows: list[dict[str, Any]],
        market_data_fresh: bool = True,
    ) -> dict[str, Any]:
        market_data = {
            str(row.get("symbol") or "").strip().upper(): dict(row)
            for row in market_rows
            if str(row.get("symbol") or "").strip()
        }
        exposure = _current_exposure(self.executor, market_data)
        self._sync_session_state(timestamp, float(exposure["current_equity_usd"]))
        session_realized_pnl = (
            self.executor.portfolio.realized_pnl
            - self._session_realized_pnl_start
        )
        approval = approve_signal_batch(
            signals,
            market_rows,
            policy=self.policy,
            live_trading_enabled=False,
            market_data_fresh=market_data_fresh,
            realized_pnl_usd=session_realized_pnl,
            unrealized_pnl_usd=exposure["unrealized_pnl_usd"],
            current_equity_usd=exposure["current_equity_usd"],
            intraday_peak_equity_usd=self._intraday_peak_equity_usd,
            existing_gross_notional_usd=exposure["gross_notional_usd"],
            existing_symbol_notional_usd=exposure["symbol_notional_usd"],
            existing_sector_notional_usd=exposure["sector_notional_usd"],
            existing_symbol_quantity=exposure["symbol_quantity"],
            existing_symbol_return_bps=exposure["symbol_return_bps"],
        )
        execution = self.executor.run_cycle(
            cycle_id=cycle_id,
            timestamp=timestamp,
            signals=approval.orders,
            market_data=market_data,
        )
        snapshot_equity = float(execution["snapshot"]["equity"])
        self._intraday_peak_equity_usd = max(
            self._intraday_peak_equity_usd,
            snapshot_equity,
        )
        return {
            "approval": _approval_to_dict(approval),
            "execution": execution,
        }

    def _sync_session_state(self, timestamp: str, current_equity_usd: float) -> None:
        session_key = str(timestamp)[:10]
        if self._session_key != session_key:
            self._session_key = session_key
            self._session_realized_pnl_start = self.executor.portfolio.realized_pnl
            self._intraday_peak_equity_usd = current_equity_usd
            return
        self._intraday_peak_equity_usd = max(
            self._intraday_peak_equity_usd,
            current_equity_usd,
        )


def load_paper_execution_gateway(
    *,
    risk_path: Path = Path("config/risk.yaml"),
    strategies_path: Path = Path("config/strategies.yaml"),
    costs_path: Path = Path("config/costs.yaml"),
) -> PaperExecutionGateway:
    policy = load_risk_policy(risk_path=risk_path, strategies_path=strategies_path)
    return PaperExecutionGateway(
        policy=policy,
        cost_config=CostModelConfig(**paper_cost_contract(costs_path)),
    )


def _current_exposure(
    executor: PaperExecutor,
    market_data: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    gross = 0.0
    by_symbol: dict[str, float] = {}
    by_sector: dict[str, float] = {}
    by_quantity: dict[str, int] = {}
    by_return_bps: dict[str, float] = {}
    for symbol, position in executor.portfolio.positions.items():
        row = market_data.get(symbol, {})
        price = float(row.get("close") or position.avg_cost)
        notional = abs(position.quantity * price)
        sector = str(row.get("sector") or "UNKNOWN")
        gross += notional
        by_symbol[symbol] = notional
        by_sector[sector] = by_sector.get(sector, 0.0) + notional
        by_quantity[symbol] = int(position.quantity)
        if position.avg_cost > 0:
            direction = 1.0 if position.quantity > 0 else -1.0
            by_return_bps[symbol] = (
                direction * (price / position.avg_cost - 1.0) * 10_000.0
            )

    marks = executor.portfolio.mark_to_market(
        {
            symbol: float(row.get("close") or executor.portfolio.positions[symbol].avg_cost)
            for symbol, row in market_data.items()
            if symbol in executor.portfolio.positions
        }
    )
    return {
        "gross_notional_usd": gross,
        "symbol_notional_usd": by_symbol,
        "sector_notional_usd": by_sector,
        "symbol_quantity": by_quantity,
        "symbol_return_bps": by_return_bps,
        "unrealized_pnl_usd": cast(float, marks["unrealized_pnl"]),
        "current_equity_usd": cast(float, marks["equity"]),
    }


def _approval_to_dict(approval: RiskApprovalResult) -> dict[str, Any]:
    return {
        "orders": approval.orders,
        "rejected": approval.rejected,
        "policy": asdict(approval.policy),
    }
