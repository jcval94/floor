from __future__ import annotations

from datetime import datetime, timezone

import pytest

import forecasting.generate_forecasts as forecast_module
from forecasting.load_models import HorizonForecast, M3Forecast
from utils.pages_publish import validate_prediction_contract


class _FakeModel:
    is_available = True
    version = "fake-v2"
    model_readout: dict = {}
    load_diagnostics: dict = {}
    m3_timing_abstention_threshold = 0.12

    def predict_d1(self, _row: dict) -> HorizonForecast:
        return HorizonForecast(99.0, 101.0, "OPEN", "CLOSE", 0.2, 0.0, 2.0)

    def predict_w1(self, _row: dict) -> HorizonForecast:
        return HorizonForecast(97.0, 103.0, "1", "5", 0.2, 0.0, 6.0)

    def predict_q1(self, _row: dict) -> HorizonForecast:
        return HorizonForecast(95.0, 105.0, "2", "10", 0.2, 0.0, 10.0)

    def predict_m3(self, _row: dict) -> M3Forecast:
        return M3Forecast(
            floor_m3=90.0,
            floor_week_m3=5,
            floor_week_m3_confidence=0.08,
            floor_week_m3_top3=[
                {"week": 5, "probability": 0.08},
                {"week": 6, "probability": 0.079},
                {"week": 4, "probability": 0.078},
            ],
            expected_return_m3=0.0,
            expected_range_m3=20.0,
        )


def test_m3_value_survives_when_timing_abstains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        forecast_module,
        "load_champion_models",
        lambda *args, **kwargs: _FakeModel(),
    )
    monkeypatch.setattr(
        forecast_module,
        "merge_market_with_ai_signal",
        lambda raw, _ai, as_of=None: dict(raw),
    )

    result = forecast_module.generate_forecasts(
        [
            {
                "symbol": "AAA",
                "close": 100.0,
                "high": 101.0,
                "low": 99.0,
                "atr_14": 2.0,
                "trend_context_m3": 0.1,
                "drawdown_13w": -0.05,
            }
        ],
        {},
        "OPEN",
        as_of=datetime(2026, 8, 24, 14, 30, tzinfo=timezone.utc),
    )

    row = result["forecasts"][0]
    assert row["floor_m3"] == 90.0
    assert row["floor_week_m3"] is None
    assert row["floor_week_m3_top3"] == []
    assert row["floor_week_m3_confidence"] == 0.08
    assert row["m3_status"] == "timing_abstained"
    assert row["m3_timing_abstention_threshold"] == 0.12
    assert "floor value remains available" in row["m3_block_reason"]


def _published_batch(m3_confidence: float) -> list[dict]:
    as_of = "2026-08-24T14:30:00+00:00"
    version = "fake-v2"
    rows = [
        {
            "symbol": "AAA",
            "horizon": "d1",
            "as_of": as_of,
            "model_version": version,
            "floor_value": 99.0,
            "ceiling_value": 101.0,
            "floor_time_bucket": "OPEN",
            "ceiling_time_bucket": "CLOSE",
        },
        {
            "symbol": "AAA",
            "horizon": "w1",
            "as_of": as_of,
            "model_version": version,
            "floor_value": 97.0,
            "ceiling_value": 103.0,
            "floor_time_bucket": "1",
            "ceiling_time_bucket": "5",
        },
        {
            "symbol": "AAA",
            "horizon": "q1",
            "as_of": as_of,
            "model_version": version,
            "floor_value": 95.0,
            "ceiling_value": 105.0,
            "floor_time_bucket": "2",
            "ceiling_time_bucket": "10",
        },
        {
            "symbol": "AAA",
            "horizon": "m3",
            "as_of": as_of,
            "model_version": version,
            "floor_m3": 90.0,
            "floor_week_m3": None,
            "floor_week_m3_confidence": m3_confidence,
            "floor_week_m3_top3": [],
            "m3_status": "timing_abstained",
            "m3_payload": {"m3_timing_abstention_threshold": 0.12},
        },
    ]
    return rows


def test_pages_accepts_truthful_m3_timing_abstention() -> None:
    audit = validate_prediction_contract(_published_batch(0.08), ["AAA"])
    assert audit["valid"] is True
    assert audit["errors"] == []


def test_pages_rejects_abstention_when_confidence_exceeds_threshold() -> None:
    audit = validate_prediction_contract(_published_batch(0.20), ["AAA"])
    assert audit["valid"] is False
    assert any(
        "abstention_confidence_not_below_threshold" in error
        for error in audit["errors"]
    )
