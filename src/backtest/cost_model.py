from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostModelConfig:
    """Explicit transaction-cost configuration for the backtester.

    All rates are expressed in basis points and are applied over notional.
    """

    commission_bps: float = 0.0
    slippage_bps: float = 0.0
    sell_fee_bps: float = 0.0
    min_commission: float = 0.0


class CostModel:
    def __init__(self, config: CostModelConfig) -> None:
        self.config = config

    def estimate(self, side: str, quantity: int, price: float) -> dict[str, float]:
        if side not in {"BUY", "SELL"}:
            raise ValueError(f"Unsupported side: {side}")
        if quantity <= 0:
            raise ValueError("Quantity must be positive")
        if price <= 0:
            raise ValueError("Price must be positive")

        notional = float(quantity) * float(price)
        commission = max(notional * self.config.commission_bps / 10_000.0, self.config.min_commission)
        slippage = notional * self.config.slippage_bps / 10_000.0
        sell_fee = notional * self.config.sell_fee_bps / 10_000.0 if side == "SELL" else 0.0
        total = commission + slippage + sell_fee
        return {
            "notional": notional,
            "commission": commission,
            "slippage": slippage,
            "sell_fee": sell_fee,
            "total_cost": total,
        }
