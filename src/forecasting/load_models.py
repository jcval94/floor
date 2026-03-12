from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HorizonForecast:
    floor: float
    ceiling: float
    floor_time: str
    ceiling_time: str
    breach_prob: float
    expected_return: float
    expected_range: float


class ChampionModelSet:
    """Lightweight champion forecaster guided by floor/ceiling research priors.

    Priors encoded from repo PDFs:
    - EVT/changepoint intuition -> wider expected ranges under higher vol regime.
    - Relative strength and AI consensus modulate directional expected return.
    """

    version = "champion-suite-v1"

    def _base(self, row: dict) -> tuple[float, float, float]:
        close = float(row["close"])
        atr = float(row.get("atr_14") or max(0.5, close * 0.01))
        vol_score = float(row.get("vol_regime_score") or 1.0)
        return close, atr, vol_score

    def predict_d1(self, row: dict) -> HorizonForecast:
        close, atr, vol = self._base(row)
        ai_bias = float(row.get("ai_consensus_score") or 0.0)
        move = atr * (1.2 + 0.4 * vol)
        floor = close - move * (1.0 - 0.15 * ai_bias)
        ceiling = close + move * (1.0 + 0.15 * ai_bias)
        return HorizonForecast(
            floor=round(floor, 4),
            ceiling=round(ceiling, 4),
            floor_time="OPEN_PLUS_2H" if vol > 1 else "OPEN_PLUS_4H",
            ceiling_time="CLOSE" if ai_bias >= 0 else "OPEN_PLUS_6H",
            breach_prob=round(min(0.95, 0.35 + 0.15 * vol), 4),
            expected_return=round((ai_bias * 0.02) + (float(row.get("rel_strength_20") or 0.0) * 0.5), 6),
            expected_range=round(max(0.01, ceiling - floor), 4),
        )

    def predict_w1(self, row: dict) -> HorizonForecast:
        close, atr, vol = self._base(row)
        rs = float(row.get("rel_strength_20") or 0.0)
        move = atr * (2.2 + 0.5 * vol)
        return HorizonForecast(
            floor=round(close - move * (1.0 - 0.1 * rs), 4),
            ceiling=round(close + move * (1.0 + 0.1 * rs), 4),
            floor_time=str(2 if rs > 0 else 1),
            ceiling_time=str(5 if rs > 0 else 4),
            breach_prob=round(min(0.97, 0.42 + 0.18 * vol), 4),
            expected_return=round(rs * 0.8, 6),
            expected_range=round(move * 2, 4),
        )

    def predict_q1(self, row: dict) -> HorizonForecast:
        close, atr, vol = self._base(row)
        momentum = float(row.get("momentum_20") or 0.0)
        move = atr * (3.6 + 0.6 * vol)
        return HorizonForecast(
            floor=round(close - move * (1.0 - 0.1 * momentum), 4),
            ceiling=round(close + move * (1.0 + 0.1 * momentum), 4),
            floor_time=str(3 if momentum > 0 else 2),
            ceiling_time=str(10 if momentum > 0 else 8),
            breach_prob=round(min(0.98, 0.5 + 0.15 * vol), 4),
            expected_return=round(momentum * 0.9, 6),
            expected_range=round(move * 2, 4),
        )


def load_champion_models() -> ChampionModelSet:
    return ChampionModelSet()
