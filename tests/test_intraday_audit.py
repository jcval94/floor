from __future__ import annotations

import json
from pathlib import Path

from floor.storage import append_jsonl
from utils.intraday_audit import build_intraday_audit


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_intraday_audit_contains_only_requested_batch_and_run_context(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    batch_id = "2026-09-23:OPEN"

    for current_batch, floor in (
        ("2026-09-22:OPEN", 90.0),
        (batch_id, 100.0),
    ):
        append_jsonl(
            data / "predictions" / "AAPL.jsonl",
            {
                "symbol": "AAPL",
                "as_of": "2026-09-23T09:30:00-04:00",
                "event_type": "OPEN",
                "horizon": "d1",
                "floor_value": floor,
                "ceiling_value": 110.0,
                "model_version": "v1",
            },
            batch_id=current_batch,
        )
        append_jsonl(
            data / "signals" / "AAPL.jsonl",
            {
                "symbol": "AAPL",
                "as_of": "2026-09-23T09:30:00-04:00",
                "horizon": "d1",
                "action": "HOLD",
                "confidence": 0.8,
            },
            batch_id=current_batch,
        )

    snapshot_id = "a" * 64
    input_snapshot = data / "snapshots" / "input_snapshots" / f"{snapshot_id}.json"
    input_snapshot.parent.mkdir(parents=True, exist_ok=True)
    input_snapshot.write_text(
        json.dumps({"input_snapshot_id": snapshot_id, "first_batch_id": batch_id}),
        encoding="utf-8",
    )
    marker = data / "snapshots" / "workflow_runs" / "intraday_2026-09-23_OPEN.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"run_id": "123", "event": "OPEN"}), encoding="utf-8")

    logs = data / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "intraday_engine_OPEN_attempt1.log").write_text(
        f"[canonical-intraday] complete input_snapshot_id={snapshot_id}\n",
        encoding="utf-8",
    )
    (logs / "baseline.env").write_text("PREDICTIONS=1\n", encoding="utf-8")
    reports = data / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "dashboard.json").write_text("{}\n", encoding="utf-8")

    output = tmp_path / "audit"
    manifest = build_intraday_audit(
        data_dir=data,
        output_dir=output,
        batch_id=batch_id,
        event="OPEN",
        session_day="2026-09-23",
        checkpoint_at="2026-09-23T09:30:00-04:00",
        required_market_session="2026-09-22",
        run_id="123",
        source_sha="abc123",
    )

    predictions = _read_jsonl(output / "evidence" / "predictions.jsonl")
    signals = _read_jsonl(output / "evidence" / "signals.jsonl")
    assert len(predictions) == 1
    assert predictions[0]["batch_id"] == batch_id
    assert predictions[0]["floor_value"] == 100.0
    assert len(signals) == 1
    assert signals[0]["batch_id"] == batch_id
    assert manifest["prediction_rows"] == 1
    assert manifest["signal_rows"] == 1
    assert manifest["input_snapshot_id"] == snapshot_id
    assert (output / "snapshots" / "workflow_run.json").is_file()
    assert (output / "snapshots" / "input_snapshot.json").is_file()
    assert (output / "reports" / "dashboard.json").is_file()
    assert (output / "manifest.json").is_file()
    assert (output / "SHA256SUMS").is_file()

    inventory_paths = {item["path"] for item in manifest["files"]}
    assert "evidence/predictions.jsonl" in inventory_paths
    assert "evidence/signals.jsonl" in inventory_paths
    assert all("app.sqlite" not in path for path in inventory_paths)
    assert all("market_data.sqlite" not in path for path in inventory_paths)


def test_intraday_audit_rejects_checkpoint_batch_mismatch(tmp_path: Path) -> None:
    data = tmp_path / "data"
    marker = data / "snapshots" / "workflow_runs" / "intraday_2026-09-23_OPEN.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("{}", encoding="utf-8")

    try:
        build_intraday_audit(
            data_dir=data,
            output_dir=tmp_path / "audit",
            batch_id="2026-09-22:OPEN",
            event="OPEN",
            session_day="2026-09-23",
            checkpoint_at="2026-09-23T09:30:00-04:00",
            required_market_session="2026-09-22",
            run_id="123",
            source_sha="abc123",
        )
    except ValueError as exc:
        assert "batch_id mismatch" in str(exc)
    else:
        raise AssertionError("batch/checkpoint mismatch must fail closed")
