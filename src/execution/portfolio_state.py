from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Position:
    quantity: int = 0
    avg_cost: float = 0.0


class PortfolioState:
    def __init__(self, initial_cash: float) -> None:
        if initial_cash <= 0:
            raise ValueError("initial_cash must be > 0")
        self.initial_cash = float(initial_cash)
        self.cash = float(initial_cash)
        self.realized_pnl = 0.0
        self.positions: dict[str, Position] = {}

    def apply_fill(self, symbol: str, side: str, quantity: int, price: float, total_cost: float) -> float:
        if quantity <= 0:
            raise ValueError("quantity must be > 0")
        if price <= 0:
            raise ValueError("price must be > 0")

        signed_qty = quantity if side == "BUY" else -quantity
        pos = self.positions.get(symbol, Position())

        realized = self._update_position(pos, signed_qty, price)
        self.realized_pnl += realized

        notional = quantity * price
        if side == "BUY":
            self.cash -= notional + total_cost
        else:
            self.cash += notional - total_cost

        if pos.quantity == 0:
            self.positions.pop(symbol, None)
        else:
            self.positions[symbol] = pos
        return realized

    def mark_to_market(self, prices: dict[str, float]) -> dict[str, object]:
        market_value = 0.0
        unrealized = 0.0
        gross = 0.0
        net = 0.0
        position_marks: dict[str, dict[str, float]] = {}

        for symbol, pos in self.positions.items():
            px = float(prices.get(symbol, pos.avg_cost))
            value = pos.quantity * px
            market_value += value
            gross += abs(value)
            net += value
            unrealized += pos.quantity * (px - pos.avg_cost)
            position_marks[symbol] = {
                "qty": pos.quantity,
                "avg_cost": pos.avg_cost,
                "mark_price": px,
            }

        equity = self.cash + market_value
        gross_exposure = 0.0 if equity == 0 else gross / equity
        net_exposure = 0.0 if equity == 0 else net / equity

        return {
            "cash": self.cash,
            "market_value": market_value,
            "equity": equity,
            "realized_pnl": self.realized_pnl,
            "unrealized_pnl": unrealized,
            "gross_exposure": gross_exposure,
            "net_exposure": net_exposure,
            "positions": position_marks,
        }

    def _update_position(self, pos: Position, signed_qty: int, price: float) -> float:
        old_qty = pos.quantity
        old_avg = pos.avg_cost
        new_qty = old_qty + signed_qty

        if old_qty == 0 or (old_qty > 0 and signed_qty > 0) or (old_qty < 0 and signed_qty < 0):
            total_notional = old_avg * abs(old_qty) + price * abs(signed_qty)
            pos.quantity = new_qty
            pos.avg_cost = total_notional / abs(new_qty)
            return 0.0

        closing_qty = min(abs(old_qty), abs(signed_qty))
        realized = closing_qty * (price - old_avg) * (1 if old_qty > 0 else -1)

        if new_qty == 0:
            pos.quantity = 0
            pos.avg_cost = 0.0
        elif (old_qty > 0 > new_qty) or (old_qty < 0 < new_qty):
            pos.quantity = new_qty
            pos.avg_cost = price
        else:
            pos.quantity = new_qty
        return realized
