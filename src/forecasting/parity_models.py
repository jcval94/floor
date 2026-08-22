from __future__ import annotations

from pathlib import Path
from typing import Any

from forecasting.load_models import ChampionModelSet, HorizonForecast, M3Forecast
from models.classic_horizon_predictor import (
    build_runtime_features,
    model_family,
    predict_family_delta,
)
from models.horizon_timing import predict_horizon_timing
from models.inference import predict_timing_week_probabilities, predict_value_floor_m3


class ParityChampionModelSet(ChampionModelSet):
    """Champion set that executes serialized training params at serving time.

    Classic floor/ceiling values and w1/q1 timing must come from trained params.
    d1 timing is explicitly allowed to be unavailable because the canonical
    training source currently contains daily OHLC bars and cannot identify an
    intraday OPEN/+2h/+4h/+6h/CLOSE event honestly.
    """

    def _predict_classic_horizon(
        self,
        row: dict,
        artifact: Any | None,
        horizon: str,
    ) -> HorizonForecast | None:
        if not isinstance(artifact, dict):
            return None

        family = model_family(str(artifact.get("model_name") or ""))
        if not family:
            return super()._predict_classic_horizon(row, artifact, horizon)

        params = artifact.get("params")
        if not isinstance(params, dict):
            raise ValueError(f"Classic champion {horizon} missing params mapping")
        floor_params = params.get("floor")
        ceiling_params = params.get("ceiling")
        if not isinstance(floor_params, dict) or not isinstance(ceiling_params, dict):
            raise ValueError(f"Classic champion {horizon} missing floor/ceiling trained params")

        features = build_runtime_features(row)
        floor_delta = predict_family_delta(family, floor_params, features)
        ceiling_delta = predict_family_delta(family, ceiling_params, features)

        close = float(row["close"])
        floor = close * (1.0 - floor_delta)
        ceiling = close * (1.0 + ceiling_delta)
        spread = max(0.01, ceiling - floor)
        expected_return = ((floor + ceiling) / 2.0 - close) / max(close, 1e-6)

        timing = params.get("timing")
        floor_time = ""
        ceiling_time = ""
        if isinstance(timing, dict):
            predicted_floor_time, _floor_prob = predict_horizon_timing(
                row,
                timing,
                horizon,
                "floor",
            )
            predicted_ceiling_time, _ceiling_prob = predict_horizon_timing(
                row,
                timing,
                horizon,
                "ceiling",
            )
            if horizon == "d1":
                floor_time = "" if predicted_floor_time is None else str(predicted_floor_time)
                ceiling_time = "" if predicted_ceiling_time is None else str(predicted_ceiling_time)
            else:
                if predicted_floor_time is None or predicted_ceiling_time is None:
                    raise ValueError(
                        f"Classic champion {horizon} has no trained timing labels; retrain required"
                    )
                floor_time = str(predicted_floor_time)
                ceiling_time = str(predicted_ceiling_time)
        elif horizon != "d1":
            raise ValueError(
                f"Classic champion {horizon} missing trained timing params; retrain required"
            )

        metrics = artifact.get("metrics", {}) if isinstance(artifact.get("metrics"), dict) else {}
        spread_mae = float(metrics.get("mae_spread") or spread / max(close, 1.0))
        breach_prob = min(0.98, max(0.05, 0.2 + spread_mae / max(close, 1.0)))

        return HorizonForecast(
            floor=round(floor, 4),
            ceiling=round(ceiling, 4),
            floor_time=floor_time,
            ceiling_time=ceiling_time,
            breach_prob=round(breach_prob, 4),
            expected_return=round(expected_return, 6),
            expected_range=round(spread, 4),
        )

    def predict_m3(self, row: dict) -> M3Forecast | None:
        required = ["close", "atr_14", "trend_context_m3", "drawdown_13w"]
        if any(row.get(key) in (None, "") for key in required):
            return None

        value_params = (
            self._value_champion.get("params", {})
            if isinstance(self._value_champion, dict)
            else {}
        )
        timing_params = (
            self._timing_champion.get("params", {})
            if isinstance(self._timing_champion, dict)
            else {}
        )
        if int(value_params.get("schema_version") or 0) != 2:
            raise ValueError(
                "m3 value champion uses deprecated absolute target schema; retrain required"
            )
        if int(timing_params.get("schema_version") or 0) != 2:
            raise ValueError(
                "m3 timing champion uses deprecated heuristic schema; retrain required"
            )

        close = float(row["close"])
        atr = float(row.get("atr_14") or max(0.5, close * 0.01))
        trend = float(row.get("trend_context_m3") or 0.0)
        dd = float(row.get("drawdown_13w") or 0.0)
        align = float(row.get("ai_horizon_alignment") or 0.0)

        floor = predict_value_floor_m3(row, self._value_champion)
        probs = predict_timing_week_probabilities(row, self._timing_champion)
        if len(probs) != 13:
            raise ValueError("m3 timing champion must return exactly 13 probabilities")

        best_idx = max(range(13), key=lambda idx: probs[idx])
        top3_idx = sorted(range(13), key=lambda idx: probs[idx], reverse=True)[:3]
        top3 = [
            {"week": idx + 1, "probability": round(probs[idx], 6)}
            for idx in top3_idx
        ]
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


def load_champion_models(model_registry_dir: Path | None = None) -> ParityChampionModelSet:
    return ParityChampionModelSet(model_registry_dir=model_registry_dir)