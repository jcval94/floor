from __future__ import annotations

from dataclasses import dataclass
from math import floor


@dataclass(frozen=True)
class ExecutionConfig:
    max_participation_rate: float = 0.1
    price_reference: str = "ohlc4"


class ExecutionSimulator:
    """Simple deterministic fill simulator.

    - Limits fills by available bar volume and participation cap.
    - Supports either OPEN or OHLC4 as execution reference.
    """

    def __init__(self, config: ExecutionConfig) -> None:
        if not 0 < config.max_participation_rate <= 1:
            raise ValueError("max_participation_rate must be in (0, 1]")
        self.config = config

    def simulate_fill(self, desired_delta_qty: int, bar: dict[str, float]) -> dict[str, float]:
        if desired_delta_qty == 0:
            return {
                "filled_qty": 0,
                "remaining_qty": 0,
                "fill_price": float(bar["close"]),
                "fill_ratio": 0.0,
            }

        abs_desired = abs(desired_delta_qty)
        side_sign = 1 if desired_delta_qty > 0 else -1
        available_volume = int(bar.get("volume", 0))
        max_fillable = floor(available_volume * self.config.max_participation_rate)
        filled_abs = min(abs_desired, max(0, max_fillable))

        if self.config.price_reference == "open":
            fill_price = float(bar["open"])
        elif self.config.price_reference == "ohlc4":
            fill_price = (float(bar["open"]) + float(bar["high"]) + float(bar["low"]) + float(bar["close"])) / 4.0
        else:
            raise ValueError(f"Unsupported price reference: {self.config.price_reference}")

        filled_qty = side_sign * filled_abs
        return {
            "filled_qty": filled_qty,
            "remaining_qty": desired_delta_qty - filled_qty,
            "fill_price": fill_price,
            "fill_ratio": 0.0 if abs_desired == 0 else filled_abs / abs_desired,
        }
