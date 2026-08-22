from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from backtest.cost_model import CostModelConfig
from execution.paper_executor import PaperExecutionConfig, PaperExecutor
from execution.risk_gateway import RiskApprovalResult, RiskPolicy, approve_signal_batch, load_risk_policy
from strategies.run_strategies import load_simple_yaml


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
        approval = approve_signal_batch(
            signals,
            market_rows,
            policy=self.policy,
            live_trading_enabled=False,
            market_data_fresh=market_data_fresh,
            realized_pnl_usd=self.executor.portfolio.realized_pnl,
            existing_gross_notional_usd=exposure["gross_notional_usd"],
            existing_symbol_notional_usd=exposure["symbol_notional_usd"],
            existing_sector_notional_usd=exposure["sector_notional_usd"],
        )
        execution = self.executor.run_cycle(
            cycle_id=cycle_id,
            timestamp=timestamp,
            signals=approval.orders,
            market_data=market_data,
        )
        return {
            "approval": _approval_to_dict(approval),
            "execution": execution,
        }


def load_paper_execution_gateway(
    *,
    risk_path: Path = Path("config/risk.yaml"),
    strategies_path: Path = Path("config/strategies.yaml"),
) -> PaperExecutionGateway:
    policy = load_risk_policy(risk_path=risk_path, strategies_path=strategies_path)
    cfg = load_simple_yaml(strategies_path)
    costs = cfg.get("costs", {})
    return PaperExecutionGateway(
        policy=policy,
        cost_config=CostModelConfig(
            commission_bps=float(costs.get("commission_bps", 0.0)),
            slippage_bps=float(costs.get("slippage_bps", 0.0)),
        ),
    )


def _current_exposure(
    executor: PaperExecutor,
    market_data: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    gross = 0.0
    by_symbol: dict[str, float] = {}
    by_sector: dict[str, float] = {}
    for symbol, position in executor.portfolio.positions.items():
        row = market_data.get(symbol, {})
        price = float(row.get("close") or position.avg_cost)
        notional = abs(position.quantity * price)
        sector = str(row.get("sector") or "UNKNOWN")
        gross += notional
        by_symbol[symbol] = notional
        by_sector[sector] = by_sector.get(sector, 0.0) + notional
    return {
        "gross_notional_usd": gross,
        "symbol_notional_usd": by_symbol,
        "sector_notional_usd": by_sector,
    }


def _approval_to_dict(approval: RiskApprovalResult) -> dict[str, Any]:
    return {
        "orders": approval.orders,
        "rejected": approval.rejected,
        "policy": asdict(approval.policy),
    }
