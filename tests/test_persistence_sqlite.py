from __future__ import annotations

import json
from pathlib import Path

from floor.reporting.generate_site_data import build_dashboard_snapshot
from floor.storage import append_jsonl


def test_append_jsonl_mirrors_prediction_to_sqlite(tmp_path: Path) -> None:
    append_jsonl(
        tmp_path / "data" / "predictions" / "AAPL.jsonl",
        {
            "symbol": "AAPL",
            "as_of": "2026-01-01T12:00:00+00:00",
            "event_type": "OPEN",
            "horizon": "d1",
            "floor_value": 100.0,
            "ceiling_value": 110.0,
            "model_version": "v1",
        },
    )

    db_path = tmp_path / "data" / "persistence" / "app.sqlite"
    assert db_path.exists()


def test_dashboard_snapshot_uses_sqlite_latest_predictions(tmp_path: Path) -> None:
    append_jsonl(
        tmp_path / "data" / "predictions" / "AAPL.jsonl",
        {
            "symbol": "AAPL",
            "as_of": "2026-01-01T12:00:00+00:00",
            "event_type": "OPEN",
            "horizon": "d1",
            "floor_value": 100.0,
            "ceiling_value": 110.0,
            "model_version": "v1",
        },
    )
    append_jsonl(
        tmp_path / "data" / "predictions" / "AAPL.jsonl",
        {
            "symbol": "AAPL",
            "as_of": "2026-01-01T13:00:00+00:00",
            "event_type": "OPEN_PLUS_2H",
            "horizon": "d1",
            "floor_value": 101.0,
            "ceiling_value": 111.0,
            "model_version": "v1",
        },
    )
    append_jsonl(
        tmp_path / "data" / "training" / "reviews.jsonl",
        {
            "as_of": "2026-01-01T13:00:00+00:00",
            "model_name": "champion-v0",
            "action": "SKIP",
            "reason": "ok",
            "data_drift": 0.1,
            "concept_drift": 0.1,
            "calibration_drift": 0.1,
            "performance_decay": 0.1,
        },
    )

    out = tmp_path / "data" / "reports" / "dashboard.json"
    build_dashboard_snapshot(tmp_path / "data", out)
    payload = json.loads(out.read_text(encoding="utf-8"))

    assert payload["latest_predictions_source"] == "sqlite"
    assert len(payload["latest_predictions"]) == 1
    assert payload["latest_predictions"][0]["floor_value"] == 101.0
