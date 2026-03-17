from __future__ import annotations

import json
from pathlib import Path

from floor.reporting.generate_site_data import build_dashboard_snapshot
from floor.persistence_db import persist_payload, stream_count
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


def test_persist_model_competition_results_to_sqlite(tmp_path: Path) -> None:
    db_path = tmp_path / "data" / "persistence" / "app.sqlite"
    persist_payload(
        db_path,
        "model_competition",
        {
            "as_of": "2026-01-01T13:00:00+00:00",
            "version": "vtest",
            "horizon": "d1",
            "model_id": "xgboost_d1",
            "model_family": "xgboost",
            "is_champion": True,
            "metrics": {
                "mae_floor": 1.2,
                "mae_ceiling": 1.4,
                "mae_spread": 0.6,
                "test_floor_coverage": 0.9,
                "test_ceiling_coverage": 0.9,
            },
        },
    )

    assert stream_count(db_path, "model_competition_results") == 1


def test_persist_model_training_cycle_to_sqlite(tmp_path: Path) -> None:
    db_path = tmp_path / "data" / "persistence" / "app.sqlite"
    persist_payload(
        db_path,
        "model_training_cycle",
        {
            "as_of": "2026-01-01T13:00:00+00:00",
            "task": "value",
            "training_mode": "retrain",
            "action": "TRAINED",
            "champion_decision": "promote",
            "model_name": "m3_value_linear",
            "model_version": "vtest",
            "retrained": True,
            "previous_champion_path": "data/training/models/value_champion.json",
            "previous_champion_version": "vprev",
            "new_champion_path": "data/training/models/value_champion.json",
            "challenger_path": "data/training/models/value_challenger_x.json",
            "metrics_path": "data/training/metrics/training_metrics_vtest.json",
            "dataset_path": "data/training/modelable_dataset.json",
            "output_dir": "data/training",
            "cv_enabled": True,
            "cv_folds": 3,
            "hyperparameter_grid": {"atr_14": [-0.8, -0.6, -0.4]},
            "tuning_summary": {"cv_enabled": True, "folds": 3, "grid_size": 10},
        },
    )

    assert stream_count(db_path, "model_training_cycles") == 1


def test_append_jsonl_prediction_count_matches_sqlite_rows(tmp_path: Path) -> None:
    pred_path = tmp_path / "data" / "predictions" / "AAPL.jsonl"
    append_jsonl(
        pred_path,
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
        pred_path,
        {
            "symbol": "AAPL",
            "as_of": "2026-01-01T13:00:00+00:00",
            "event_type": "OPEN_PLUS_2H",
            "horizon": "w1",
            "floor_value": 99.5,
            "ceiling_value": 112.0,
            "model_version": "v1",
        },
    )

    line_count = len([line for line in pred_path.read_text(encoding="utf-8").splitlines() if line.strip()])
    db_path = tmp_path / "data" / "persistence" / "app.sqlite"
    assert stream_count(db_path, "predictions") == line_count == 2
