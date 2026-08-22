from __future__ import annotations

from pathlib import Path
from typing import Any

from forecasting.load_models import ChampionModelSet, HorizonForecast
from models.classic_horizon_predictor import (
    build_runtime_features,
    model_family,
    predict_family_delta,
)


class ParityChampionModelSet(ChampionModelSet):
    """Champion set that executes serialized classic-horizon params at serving time.

    Older heuristic artifacts without nested floor/ceiling params continue through
    the compatibility implementation in ChampionModelSet. Competition artifacts
    produced by train_classic_horizons are executed from their actual learned
    parameters rather than their aggregate median deltas.
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
        params = artifact.get("params")
        if not family or not isinstance(params, dict):
            return super()._predict_classic_horizon(row, artifact, horizon)

        floor_params = params.get("floor")
        ceiling_params = params.get("ceiling")
        if not isinstance(floor_params, dict) or not isinstance(ceiling_params, dict):
            return super()._predict_classic_horizon(row, artifact, horizon)

        features = build_runtime_features(row)
        floor_delta = predict_family_delta(family, floor_params, features)
        ceiling_delta = predict_family_delta(family, ceiling_params, features)

        close = float(row["close"])
        floor = close * (1.0 - floor_delta)
        ceiling = close * (1.0 + ceiling_delta)
        spread = max(0.01, ceiling - floor)
        expected_return = ((floor + ceiling) / 2.0 - close) / max(close, 1e-6)

        metrics = artifact.get("metrics", {}) if isinstance(artifact.get("metrics"), dict) else {}
        spread_mae = float(metrics.get("mae_spread") or spread / max(close, 1.0))
        breach_prob = min(0.98, max(0.05, 0.2 + spread_mae / max(close, 1.0)))
        floor_time, ceiling_time = _family_times(family, horizon)

        return HorizonForecast(
            floor=round(floor, 4),
            ceiling=round(ceiling, 4),
            floor_time=floor_time,
            ceiling_time=ceiling_time,
            breach_prob=round(breach_prob, 4),
            expected_return=round(expected_return, 6),
            expected_range=round(spread, 4),
        )


def _family_times(family: str, horizon: str) -> tuple[str, str]:
    times: dict[str, dict[str, tuple[str, str]]] = {
        "d1": {
            "evt_changepoint_hybrid": ("OPEN_PLUS_2H", "CLOSE"),
            "xgboost": ("OPEN_PLUS_4H", "OPEN_PLUS_6H"),
            "lstm_sequence": ("OPEN_PLUS_2H", "OPEN_PLUS_6H"),
            "quantile_elastic_net": ("OPEN_PLUS_4H", "CLOSE"),
        },
        "w1": {
            "evt_changepoint_hybrid": ("2", "5"),
            "xgboost": ("3", "4"),
            "lstm_sequence": ("1", "5"),
            "quantile_elastic_net": ("2", "4"),
        },
        "q1": {
            "evt_changepoint_hybrid": ("15", "45"),
            "xgboost": ("20", "40"),
            "lstm_sequence": ("10", "45"),
            "quantile_elastic_net": ("15", "35"),
        },
    }
    return times.get(horizon, {}).get(family, ("", ""))


def load_champion_models(model_registry_dir: Path | None = None) -> ParityChampionModelSet:
    return ParityChampionModelSet(model_registry_dir=model_registry_dir)
