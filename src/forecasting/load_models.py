from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class HorizonForecast:
    floor: float
    ceiling: float
    floor_time: str
    ceiling_time: str
    breach_prob: float
    expected_return: float
    expected_range: float


@dataclass(frozen=True)
class M3Forecast:
    floor_m3: float
    floor_week_m3: int
    floor_week_m3_confidence: float
    floor_week_m3_top3: list[dict]
    expected_return_m3: float
    expected_range_m3: float


class ChampionModelSet:
    """Champion forecaster with d1/w1/q1 compatibility and m3 extension."""

    version = "champion-suite-v2-m3"

    def __init__(self, model_registry_dir: Path | None = None) -> None:
        self._registry = model_registry_dir or Path("data/training/models")
        self._value_champion = self._load_json(self._registry / "value_champion.json")
        self._timing_champion = self._load_json(self._registry / "timing_champion.json")

    @property
    def is_available(self) -> bool:
        """Only publish forecasts when both trained artifacts are available."""
        return self._value_champion is not None and self._timing_champion is not None

    @staticmethod
    def _load_json(path: Path) -> dict | None:
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

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

    def predict_m3(self, row: dict) -> M3Forecast | None:
        required = ["close", "atr_14", "trend_context_m3", "drawdown_13w", "ai_horizon_alignment"]
        if any(row.get(k) in (None, "") for k in required):
            return None

        close = float(row["close"])
        atr = float(row.get("atr_14") or max(0.5, close * 0.01))
        trend = float(row.get("trend_context_m3") or 0.0)
        dd = float(row.get("drawdown_13w") or 0.0)
        align = float(row.get("ai_horizon_alignment") or 0.0)

        if self._value_champion:
            params = self._value_champion.get("params", {})
            w = params.get("weights", {})
            bias = float(params.get("bias", close * 0.95))
            floor_raw = bias + sum(float(row.get(k, 0.0) or 0.0) * float(v) for k, v in w.items())
            floor = float(params.get("calibration_scale", 1.0)) * floor_raw
        else:
            floor = close - atr * (8.0 + 2.5 * max(0.0, 1 - trend))

        center = 7 - int(max(-3, min(3, dd * 10)))
        center = max(1, min(13, center))
        scores = [1.8 - 0.25 * abs(w - center) + 0.35 * align + 0.15 * trend for w in range(1, 14)]
        exps = [pow(2.718281828, s) for s in scores]
        denom = sum(exps) or 1.0
        probs = [x / denom for x in exps]

        if self._timing_champion:
            reliability = self._timing_champion.get("params", {}).get("calibrator_reliability", {})
            if reliability:
                calibrated = []
                for p in probs:
                    idx = min(9, int(max(0.0, min(1.0, p)) * 10))
                    calibrated.append(float(reliability.get(str(idx), reliability.get(idx, p))))
                s = sum(calibrated)
                probs = [p / s for p in calibrated] if s > 0 else probs

        best_idx = max(range(13), key=lambda i: probs[i])
        top3_idx = sorted(range(13), key=lambda i: probs[i], reverse=True)[:3]
        top3 = [{"week": i + 1, "probability": round(probs[i], 6)} for i in top3_idx]

        expected_return = round(0.5 * trend + 0.2 * align - 0.15 * abs(dd), 6)
        expected_range = round(max(0.01, atr * (10 + 2 * (1 + abs(dd)))), 4)

        return M3Forecast(
            floor_m3=round(floor, 4),
            floor_week_m3=best_idx + 1,
            floor_week_m3_confidence=round(probs[best_idx], 6),
            floor_week_m3_top3=top3,
            expected_return_m3=expected_return,
            expected_range_m3=expected_range,
        )


def load_champion_models() -> ChampionModelSet:
    return ChampionModelSet()
