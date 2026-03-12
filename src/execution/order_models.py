from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(str, Enum):
    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"


@dataclass
class Order:
    order_id: str
    cycle_id: str
    strategy_id: str
    symbol: str
    side: OrderSide
    quantity: int
    filled_quantity: int = 0
    status: OrderStatus = OrderStatus.NEW
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        r = asdict(self)
        r["side"] = self.side.value
        r["status"] = self.status.value
        return r


@dataclass
class Fill:
    fill_id: str
    order_id: str
    cycle_id: str
    symbol: str
    side: OrderSide
    quantity: int
    price: float
    notional: float
    commission: float
    slippage: float
    sell_fee: float
    total_cost: float
    filled_at: str

    def to_record(self) -> dict[str, Any]:
        r = asdict(self)
        r["side"] = self.side.value
        return r


@dataclass
class Trade:
    trade_id: str
    cycle_id: str
    symbol: str
    side: OrderSide
    quantity: int
    avg_price: float
    fees: float
    realized_pnl: float
    opened_at: str

    def to_record(self) -> dict[str, Any]:
        r = asdict(self)
        r["side"] = self.side.value
        return r


@dataclass
class PortfolioSnapshot:
    snapshot_id: str
    cycle_id: str
    timestamp: str
    cash: float
    market_value: float
    equity: float
    realized_pnl: float
    unrealized_pnl: float
    gross_exposure: float
    net_exposure: float
    positions: dict[str, dict[str, float]]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


MINIMUM_SCHEMAS: dict[str, dict[str, str]] = {
    "orders": {
        "order_id": "str",
        "cycle_id": "str",
        "strategy_id": "str",
        "symbol": "str",
        "side": "BUY|SELL",
        "quantity": "int",
        "filled_quantity": "int",
        "status": "NEW|PARTIALLY_FILLED|FILLED|CANCELED|REJECTED",
        "created_at": "ISO8601 str",
        "updated_at": "ISO8601 str",
        "metadata": "dict",
    },
    "fills": {
        "fill_id": "str",
        "order_id": "str",
        "cycle_id": "str",
        "symbol": "str",
        "side": "BUY|SELL",
        "quantity": "int",
        "price": "float",
        "notional": "float",
        "commission": "float",
        "slippage": "float",
        "sell_fee": "float",
        "total_cost": "float",
        "filled_at": "ISO8601 str",
    },
    "trades": {
        "trade_id": "str",
        "cycle_id": "str",
        "symbol": "str",
        "side": "BUY|SELL",
        "quantity": "int",
        "avg_price": "float",
        "fees": "float",
        "realized_pnl": "float",
        "opened_at": "ISO8601 str",
    },
    "portfolio_snapshots": {
        "snapshot_id": "str",
        "cycle_id": "str",
        "timestamp": "ISO8601 str",
        "cash": "float",
        "market_value": "float",
        "equity": "float",
        "realized_pnl": "float",
        "unrealized_pnl": "float",
        "gross_exposure": "float",
        "net_exposure": "float",
        "positions": "dict(symbol -> {qty:int, avg_cost:float, mark_price:float})",
    },
}
