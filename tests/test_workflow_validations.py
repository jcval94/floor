from __future__ import annotations

import json
from pathlib import Path

import pytest

from floor.storage import append_jsonl
from utils.workflow_validations import (
    capture_baseline,
    validate_deltas,
    validate_json_file,
    validate_latest_payload,
    validate_prediction_quality,
)


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


def test_validate_prediction_quality_valid_rows(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    append_jsonl(
        data_dir / "predictions" / "AAPL.jsonl",
        {
            "symbol": "AAPL",
            "action": "BUY",
            "floor_d1": 100.0,
            "ceiling_d1": 110.0,
            "expected_return_d1": 0.012,
            "floor_w1": 98.0,
            "ceiling_w1": 112.0,
            "expected_return_w1": 0.018,
            "floor_q1": 95.0,
            "ceiling_q1": 120.0,
            "expected_return_q1": 0.026,
            "m3_status": "ok",
            "m3_block_reason": None,
        },
    )

    out = validate_prediction_quality(
        data_dir=data_dir,
        stream="predictions",
        max_m3_blocked_ratio=0.4,
        min_action_consistency_ratio=0.7,
        action_return_tolerance=0.0,
        sample_limit=3,
    )

    assert out["rows"] == 1
    assert out["m3_blocked_ratio"] == 0.0
    assert out["action_consistency_ratio"] == 1.0


def test_validate_prediction_quality_invalid_floor_ceiling_fail_fast(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    append_jsonl(
        data_dir / "predictions" / "bad_band.jsonl",
        {
            "symbol": "BAD",
            "action": "BUY",
            "floor_d1": 110.0,
            "ceiling_d1": 100.0,
            "expected_return_d1": 0.01,
            "floor_w1": 98.0,
            "ceiling_w1": 111.0,
            "expected_return_w1": 0.02,
            "floor_q1": 95.0,
            "ceiling_q1": 120.0,
            "expected_return_q1": 0.03,
            "m3_status": "ok",
            "m3_block_reason": None,
        },
    )

    with pytest.raises(SystemExit, match=r"::error::valor falso: floor_value"):
        validate_prediction_quality(
            data_dir=data_dir,
            stream="predictions",
            max_m3_blocked_ratio=0.4,
            min_action_consistency_ratio=0.7,
            action_return_tolerance=0.0,
            sample_limit=2,
        )


def test_validate_prediction_quality_invalid_blocked_ratio(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    append_jsonl(
        data_dir / "predictions" / "blocked.jsonl",
        {
            "symbol": "AAPL",
            "action": "BUY",
            "floor_d1": 100.0,
            "ceiling_d1": 110.0,
            "expected_return_d1": 0.01,
            "floor_w1": 98.0,
            "ceiling_w1": 112.0,
            "expected_return_w1": 0.02,
            "floor_q1": 97.0,
            "ceiling_q1": 119.0,
            "expected_return_q1": 0.03,
            "m3_status": "blocked",
            "m3_block_reason": "insufficient_data",
        },
    )
    append_jsonl(
        data_dir / "predictions" / "ok.jsonl",
        {
            "symbol": "MSFT",
            "action": "BUY",
            "floor_d1": 200.0,
            "ceiling_d1": 210.0,
            "expected_return_d1": 0.02,
            "floor_w1": 195.0,
            "ceiling_w1": 220.0,
            "expected_return_w1": 0.03,
            "floor_q1": 190.0,
            "ceiling_q1": 230.0,
            "expected_return_q1": 0.04,
            "m3_status": "ok",
            "m3_block_reason": None,
        },
    )

    with pytest.raises(SystemExit, match=r"::error::valor falso: ratio m3_status=blocked"):
        validate_prediction_quality(
            data_dir=data_dir,
            stream="predictions",
            max_m3_blocked_ratio=0.4,
            min_action_consistency_ratio=0.7,
            action_return_tolerance=0.0,
            sample_limit=2,
        )


def test_validate_prediction_quality_invalid_action_consistency(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    append_jsonl(
        data_dir / "predictions" / "inconsistent.jsonl",
        {
            "symbol": "AAPL",
            "action": "BUY",
            "floor_d1": 100.0,
            "ceiling_d1": 110.0,
            "expected_return_d1": -0.01,
            "floor_w1": 98.0,
            "ceiling_w1": 112.0,
            "expected_return_w1": -0.02,
            "floor_q1": 97.0,
            "ceiling_q1": 119.0,
            "expected_return_q1": -0.03,
            "m3_status": "ok",
            "m3_block_reason": None,
        },
    )

    with pytest.raises(SystemExit, match=r"::error::valor falso: consistencia acción/retorno"):
        validate_prediction_quality(
            data_dir=data_dir,
            stream="predictions",
            max_m3_blocked_ratio=1.0,
            min_action_consistency_ratio=1.0,
            action_return_tolerance=0.0,
            sample_limit=2,
        )
