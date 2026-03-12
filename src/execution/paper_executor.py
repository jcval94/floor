from __future__ import annotations

from dataclasses import dataclass
from math import floor

from backtest.cost_model import CostModel, CostModelConfig
from execution.order_models import Fill, Order, OrderSide, OrderStatus, PortfolioSnapshot, Trade
from execution.portfolio_state import PortfolioState


@dataclass(frozen=True)
class PaperExecutionConfig:
    paper_trading_enabled: bool = True
    live_trading_enabled: bool = False
    max_participation_rate: float = 0.1
    price_reference: str = "ohlc4"


class PaperExecutor:
    def __init__(self, config: PaperExecutionConfig, cost_config: CostModelConfig, initial_cash: float) -> None:
        if not config.paper_trading_enabled:
            raise ValueError("paper_trading_enabled must be True")
        if config.live_trading_enabled:
            raise ValueError("live_trading_enabled must be False by default for paper execution")
        if not 0 < config.max_participation_rate <= 1:
            raise ValueError("max_participation_rate must be in (0, 1]")

        self.config = config
        self.cost_model = CostModel(cost_config)
        self.portfolio = PortfolioState(initial_cash=initial_cash)

        self.order_book: dict[str, Order] = {}
        self.fills: list[Fill] = []
        self.trades: list[Trade] = []
        self.snapshots: list[PortfolioSnapshot] = []
        self.cash_ledger: list[dict] = []
        self.executed_cycles: set[str] = set()

        self._order_seq = 0
        self._fill_seq = 0
        self._trade_seq = 0
        self._snapshot_seq = 0

    def run_cycle(self, cycle_id: str, timestamp: str, signals: list[dict], market_data: dict[str, dict]) -> dict:
        if cycle_id in self.executed_cycles:
            raise ValueError(f"Cycle {cycle_id} already executed")
        self.executed_cycles.add(cycle_id)

        orders = self._create_orders(cycle_id, timestamp, signals)
        for order in orders:
            self._simulate_and_apply(order, timestamp, market_data)

        prices = {s: float(bar["close"]) for s, bar in market_data.items()}
        snap_data = self.portfolio.mark_to_market(prices)
        snapshot = PortfolioSnapshot(
            snapshot_id=self._next_snapshot_id(),
            cycle_id=cycle_id,
            timestamp=timestamp,
            cash=float(snap_data["cash"]),
            market_value=float(snap_data["market_value"]),
            equity=float(snap_data["equity"]),
            realized_pnl=float(snap_data["realized_pnl"]),
            unrealized_pnl=float(snap_data["unrealized_pnl"]),
            gross_exposure=float(snap_data["gross_exposure"]),
            net_exposure=float(snap_data["net_exposure"]),
            positions=snap_data["positions"],
        )
        self.snapshots.append(snapshot)

        return {
            "orders": [o.to_record() for o in orders],
            "fills": [f.to_record() for f in self.fills if f.cycle_id == cycle_id],
            "trades": [t.to_record() for t in self.trades if t.cycle_id == cycle_id],
            "snapshot": snapshot.to_record(),
        }

    def _create_orders(self, cycle_id: str, timestamp: str, signals: list[dict]) -> list[Order]:
        orders: list[Order] = []
        for s in signals:
            qty = int(s.get("quantity", 0))
            if qty <= 0:
                continue
            side = OrderSide(str(s["side"]).upper())
            self._order_seq += 1
            order = Order(
                order_id=f"ord_{self._order_seq}",
                cycle_id=cycle_id,
                strategy_id=str(s["strategy_id"]),
                symbol=str(s["symbol"]),
                side=side,
                quantity=qty,
                created_at=timestamp,
                updated_at=timestamp,
                metadata=dict(s.get("metadata", {})),
            )
            self.order_book[order.order_id] = order
            orders.append(order)
        return orders

    def _simulate_and_apply(self, order: Order, timestamp: str, market_data: dict[str, dict]) -> None:
        bar = market_data.get(order.symbol)
        if bar is None:
            order.status = OrderStatus.REJECTED
            order.updated_at = timestamp
            return

        available = floor(int(bar.get("volume", 0)) * self.config.max_participation_rate)
        fill_qty = min(order.quantity, max(0, available))

        if self.config.price_reference == "open":
            px = float(bar["open"])
        elif self.config.price_reference == "ohlc4":
            px = (float(bar["open"]) + float(bar["high"]) + float(bar["low"]) + float(bar["close"])) / 4.0
        else:
            raise ValueError(f"Unsupported price_reference: {self.config.price_reference}")

        if fill_qty == 0:
            order.status = OrderStatus.CANCELED
            order.updated_at = timestamp
            return

        side_value = order.side.value
        costs = self.cost_model.estimate(side_value, fill_qty, px)

        if side_value == "BUY":
            max_affordable = floor(max(self.portfolio.cash - costs["total_cost"], 0.0) / px)
            fill_qty = min(fill_qty, max_affordable)
            if fill_qty <= 0:
                order.status = OrderStatus.REJECTED
                order.updated_at = timestamp
                return
            costs = self.cost_model.estimate(side_value, fill_qty, px)

        self._fill_seq += 1
        fill = Fill(
            fill_id=f"fill_{self._fill_seq}",
            order_id=order.order_id,
            cycle_id=order.cycle_id,
            symbol=order.symbol,
            side=order.side,
            quantity=fill_qty,
            price=px,
            notional=fill_qty * px,
            commission=costs["commission"],
            slippage=costs["slippage"],
            sell_fee=costs["sell_fee"],
            total_cost=costs["total_cost"],
            filled_at=timestamp,
        )
        self.fills.append(fill)

        order.filled_quantity += fill_qty
        order.updated_at = timestamp
        order.status = OrderStatus.FILLED if order.filled_quantity >= order.quantity else OrderStatus.PARTIALLY_FILLED

        realized = self.portfolio.apply_fill(order.symbol, side_value, fill_qty, px, fill.total_cost)

        self._trade_seq += 1
        trade = Trade(
            trade_id=f"trd_{self._trade_seq}",
            cycle_id=order.cycle_id,
            symbol=order.symbol,
            side=order.side,
            quantity=fill_qty,
            avg_price=px,
            fees=fill.total_cost,
            realized_pnl=realized,
            opened_at=timestamp,
        )
        self.trades.append(trade)

        cash_after = self.portfolio.cash
        self.cash_ledger.append(
            {
                "cycle_id": order.cycle_id,
                "order_id": order.order_id,
                "fill_id": fill.fill_id,
                "timestamp": timestamp,
                "cash_after": cash_after,
                "cash_delta": -(fill.notional + fill.total_cost) if side_value == "BUY" else (fill.notional - fill.total_cost),
            }
        )

    def _next_snapshot_id(self) -> str:
        self._snapshot_seq += 1
        return f"snap_{self._snapshot_seq}"
