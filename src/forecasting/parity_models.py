from __future__ import annotations

from pathlib import Path
from typing import Any

from forecasting.load_models import ChampionModelSet, HorizonForecast, M3Forecast
from models.classic_horizon_predictor import (
    build_runtime_features,
    model_family,
    predict_family_delta,
    validate_family_params,
)
from models.horizon_timing import predict_horizon_timing
from models.inference import predict_timing_week_probabilities, predict_value_floor_m3

DEFAULT_M3_TIMING_ABSTENTION_THRESHOLD = 0.12


class ParityChampionModelSet(ChampionModelSet):
    """Champion set that executes serialized training params at serving time.

    Classic floor/ceiling values and timing come from trained params. Confidence
    is the empirical interval non-breach rate measured on the dedicated
    validation holdout; it is not fabricated from MAE. This model set does not
    claim to predict directional return.
    """

    @property
    def m3_timing_abstention_threshold(self) -> float:
        """Return the trained timing abstention threshold with a safe default."""

        metrics = (
            self._timing_champion.get("metrics", {})
            if isinstance(self._timing_champion, dict)
            else {}
        )
        raw = (
            metrics.get("abstention_threshold")
            if isinstance(metrics, dict)
            else None
        )
        if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
            threshold = DEFAULT_M3_TIMING_ABSTENTION_THRESHOLD
        else:
            try:
                threshold = float(raw)
            except (TypeError, ValueError):
                threshold = DEFAULT_M3_TIMING_ABSTENTION_THRESHOLD
        if not 0.0 <= threshold <= 1.0:
            threshold = DEFAULT_M3_TIMING_ABSTENTION_THRESHOLD
        return threshold

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
        if int(params.get("schema_version") or 0) != 2:
            raise ValueError(
                f"Classic champion {horizon} uses unsupported schema; retrain required"
            )
        floor_params = params.get("floor")
        ceiling_params = params.get("ceiling")
        if not isinstance(floor_params, dict) or not isinstance(ceiling_params, dict):
            raise ValueError(
                f"Classic champion {horizon} missing floor/ceiling trained params"
            )

        validation_key = (id(floor_params), id(ceiling_params), family)
        validated: set[tuple[int, int, str]] = getattr(
            self, "_validated_classic_heads", set()
        )
        if validation_key not in validated:
            validate_family_params(family, floor_params)
            validate_family_params(family, ceiling_params)
            validated.add(validation_key)
            self._validated_classic_heads = validated

        features = build_runtime_features(row)
        floor_delta = predict_family_delta(
            family, floor_params, features, validate=False
        )
        ceiling_delta = predict_family_delta(
            family, ceiling_params, features, validate=False
        )

        close = float(row["close"])
        floor = close * (1.0 - floor_delta)
        ceiling = close * (1.0 + ceiling_delta)
        spread = max(0.01, ceiling - floor)

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
                floor_time = (
                    "" if predicted_floor_time is None else str(predicted_floor_time)
                )
                ceiling_time = (
                    "" if predicted_ceiling_time is None else str(predicted_ceiling_time)
                )
            else:
                if predicted_floor_time is None or predicted_ceiling_time is None:
                    raise ValueError(
                        f"Classic champion {horizon} has no trained timing labels; "
                        "retrain required"
                    )
                floor_time = str(predicted_floor_time)
                ceiling_time = str(predicted_ceiling_time)
        elif horizon != "d1":
            raise ValueError(
                f"Classic champion {horizon} missing trained timing params; retrain required"
            )

        calibration = params.get("confidence_calibration")
        if not isinstance(calibration, dict):
            raise ValueError(
                f"Classic champion {horizon} missing empirical confidence calibration; "
                "retrain required"
            )
        if calibration.get("method") != "validation_empirical_interval_breach":
            raise ValueError(
                f"Classic champion {horizon} confidence calibration method unsupported"
            )
        raw_breach = calibration.get("breach_probability")
        if not isinstance(raw_breach, (int, float, str, bytes, bytearray)):
            raise ValueError(
                f"Classic champion {horizon} missing numeric empirical breach probability"
            )
        try:
            breach_prob = float(raw_breach)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Classic champion {horizon} missing numeric empirical breach probability"
            ) from exc
        if not 0.0 <= breach_prob <= 1.0:
            raise ValueError(
                f"Classic champion {horizon} empirical breach probability out of range"
            )

        return HorizonForecast(
            floor=round(floor, 4),
            ceiling=round(ceiling, 4),
            floor_time=floor_time,
            ceiling_time=ceiling_time,
            breach_prob=round(breach_prob, 4),
            expected_return=0.0,
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
        dd = float(row.get("drawdown_13w") or 0.0)

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
        expected_range = round(max(0.01, atr * (10 + 2 * (1 + abs(dd)))), 4)
        return M3Forecast(
            floor_m3=round(floor, 4),
            floor_week_m3=best_idx + 1,
            floor_week_m3_confidence=round(probs[best_idx], 6),
            floor_week_m3_top3=top3,
            expected_return_m3=0.0,
            expected_range_m3=expected_range,
        )


def load_champion_models(
    model_registry_dir: Path | None = None,
) -> ParityChampionModelSet:
    return ParityChampionModelSet(model_registry_dir=model_registry_dir)
