from __future__ import annotations

import json
from pathlib import Path

from floor.storage import append_jsonl
from utils.workflow_validations import capture_baseline, validate_deltas, validate_json_file, validate_latest_payload


def test_capture_and_validate_deltas(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    db_path = data_dir / "persistence" / "app.sqlite"

    baseline = capture_baseline(db_path, data_dir, ["predictions", "signals"])

    append_jsonl(
        data_dir / "predictions" / "AAPL.jsonl",
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
        data_dir / "signals" / "AAPL.jsonl",
        {
            "symbol": "AAPL",
            "as_of": "2026-01-01T12:00:00+00:00",
            "horizon": "d1",
            "action": "BUY",
            "confidence": 0.7,
        },
    )

    deltas = validate_deltas(
        db_path=db_path,
        data_dir=data_dir,
        streams=["predictions", "signals"],
        baseline=baseline,
        require_positive={"predictions", "signals"},
    )

    assert deltas["delta_sqlite_predictions"] == 1
    assert deltas["delta_files_predictions"] == 1
    assert deltas["delta_sqlite_signals"] == 1
    assert deltas["delta_files_signals"] == 1


def test_validate_latest_payload_required_fields(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    path = data_dir / "predictions" / "AAPL.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"symbol": "AAPL", "model_version": "vx"}) + "\n", encoding="utf-8")

    out = validate_latest_payload(data_dir=data_dir, stream="predictions", required_fields=["model_version"])
    assert out["rows"] == 1
    assert out["payload"]["model_version"] == "vx"


def test_validate_json_file_required_fields(tmp_path: Path) -> None:
    path = tmp_path / "value_champion.json"
    path.write_text(json.dumps({"model_name": "m3_value_linear", "version": "v1"}), encoding="utf-8")

    payload = validate_json_file(path, ["model_name", "version"])
    assert payload["version"] == "v1"
