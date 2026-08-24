from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from floor.prediction_reconciliation import prediction_key
from floor.runtime_retention import compact_runtime_state


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_runtime_retention_drops_only_old_resolved_predictions(tmp_path: Path) -> None:
    data = tmp_path / "data"
    predictions_path = data / "predictions" / "AAA.jsonl"
    old_resolved = {
        "batch_id": "2026-01-02:OPEN",
        "symbol": "AAA",
        "horizon": "m3",
        "as_of": "2026-01-02T14:30:00+00:00",
        "model_version": "v2",
    }
    old_unresolved = {
        "batch_id": "2026-01-03:OPEN",
        "symbol": "AAA",
        "horizon": "m3",
        "as_of": "2026-01-03T14:30:00+00:00",
        "model_version": "v2",
    }
    recent = {
        "batch_id": "2026-08-20:OPEN",
        "symbol": "AAA",
        "horizon": "m3",
        "as_of": "2026-08-20T14:30:00+00:00",
        "model_version": "v2",
    }
    _write_jsonl(predictions_path, [old_resolved, old_unresolved, recent])

    _write_jsonl(
        data / "predictions" / "reconciliations" / "AAA.jsonl",
        [
            {
                "prediction_key": prediction_key(old_resolved),
                "predicted_as_of": old_resolved["as_of"],
                "resolved_at": "2026-04-15T20:00:00+00:00",
            }
        ],
    )
    signals_path = data / "signals" / "AAA.jsonl"
    _write_jsonl(
        signals_path,
        [
            {"as_of": "2025-10-01T14:30:00+00:00", "symbol": "AAA"},
            {"as_of": "2026-08-20T14:30:00+00:00", "symbol": "AAA"},
        ],
    )

    training_rows = data / "training" / "yahoo_market_rows.jsonl"
    _write_jsonl(
        training_rows,
        [{"timestamp": "2025-01-01T00:00:00+00:00", "symbol": "AAA"}],
    )
    before_training = training_rows.read_text(encoding="utf-8")

    result = compact_runtime_state(
        data,
        now=datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc),
    )

    remaining = _read_jsonl(predictions_path)
    assert [row["batch_id"] for row in remaining] == [
        "2026-01-03:OPEN",
        "2026-08-20:OPEN",
    ]
    assert result["predictions"]["removed_resolved_old"] == 1
    assert result["predictions"]["old_unresolved_kept"] == 1
    assert len(_read_jsonl(signals_path)) == 1
    assert training_rows.read_text(encoding="utf-8") == before_training
    retention_report = data / "metrics" / "runtime_retention_latest.json"
    assert retention_report.exists()
    report = json.loads(retention_report.read_text(encoding="utf-8"))
    assert report["safety"]["old_unresolved_predictions_are_retained"] is True


def test_strategy_league_model_and_audit_are_excluded_from_snapshot_pruning(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    league_root = data / "metrics" / "strategy_league"
    model = league_root / "models" / "weekly_opportunity_challenger.json"
    history = league_root / "runs" / "strategy_league_v1" / "history.jsonl"
    model.parent.mkdir(parents=True, exist_ok=True)
    history.parent.mkdir(parents=True, exist_ok=True)
    model.write_text('{"version":"frozen-v1"}\n', encoding="utf-8")
    history.write_text('{"event":"GENESIS"}\n', encoding="utf-8")

    generic_metrics: list[Path] = []
    for idx in range(7):
        path = data / "metrics" / f"old_metric_{idx}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
        generic_metrics.append(path)

    old_timestamp = datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp()
    for path in [model, history, *generic_metrics]:
        os.utime(path, (old_timestamp, old_timestamp))

    result = compact_runtime_state(
        data,
        now=datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc),
    )

    assert model.exists()
    assert history.exists()
    assert result["snapshot_files"]["metrics"]["protected"] >= 2
    assert result["snapshot_files"]["metrics"]["removed"] >= 1
    assert result["safety"]["strategy_league_evidence_is_retained"] is True


def test_runtime_retention_fails_closed_on_malformed_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "data" / "signals" / "AAA.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"as_of":"2026-08-20T00:00:00+00:00"}\n{broken\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Malformed runtime JSONL"):
        compact_runtime_state(
            tmp_path / "data",
            now=datetime(2026, 8, 24, tzinfo=timezone.utc),
        )
