from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HorizonPrediction:
    floor_value: float
    ceiling_value: float
    floor_bucket: str
    ceiling_bucket: str
    floor_bucket_prob: float
    ceiling_bucket_prob: float


class ChampionModel:
    """Baseline deterministic model placeholder for reproducible CI runs."""

    version = "champion-v0"

    def predict(self, symbol: str, horizon: str, event_type: str) -> HorizonPrediction:
        base = 100.0 + (hash(symbol + horizon + event_type) % 100) / 10
        return HorizonPrediction(
            floor_value=round(base * 0.97, 2),
            ceiling_value=round(base * 1.03, 2),
            floor_bucket="OPEN_PLUS_2H" if horizon == "d1" else "1",
            ceiling_bucket="CLOSE" if horizon == "d1" else "5" if horizon == "w1" else "10",
            floor_bucket_prob=0.62,
            ceiling_bucket_prob=0.59,
        )
