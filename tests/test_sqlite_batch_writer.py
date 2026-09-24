from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from floor import persistence_db as db_module
from floor.persistence_db import PersistenceWriter, persist_payload
from floor.persistence_hydration import hydrate_persistence_from_jsonl
from floor.storage import append_jsonl


def _prediction(symbol: str, horizon: str = "d1") -> dict:
    return {
        "symbol": symbol,
        "as_of": "2026-09-23T09:30:00-04:00",
        "event_type": "OPEN",
        "horizon": horizon,
        "model_version": "v1",
        "floor_value": 100.0,
        "ceiling_value": 110.0,
    }


def _signal(symbol: str) -> dict:
    return {
        "symbol": symbol,
        "as_of": "2026-09-23T09:30:00-04:00",
        "horizon": "d1",
        "action": "HOLD",
        "confidence": 0.8,
    }


def test_single_batch_connection_initialization_and_exact_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = tmp_path / "data"
    db = data / "persistence" / "app.sqlite"
    connect_count = 0
    schema_count = 0
    real_connect = db_module._connect
    real_schema = db_module._ensure_schema

    def measured_connect(db_path: Path) -> sqlite3.Connection:
        nonlocal connect_count
        connect_count += 1
        return real_connect(db_path)

    def measured_schema(conn: sqlite3.Connection) -> None:
        nonlocal schema_count
        schema_count += 1
        real_schema(conn)

    monkeypatch.setattr(db_module, "_connect", measured_connect)
    monkeypatch.setattr(db_module, "_ensure_schema", measured_schema)

    with PersistenceWriter(db) as writer:
        for number in range(50):
            symbol = f"SYM{number:03d}"
            assert append_jsonl(
                data / "predictions" / f"{symbol}.jsonl",
                _prediction(symbol),
                batch_id="2026-09-23:OPEN",
                writer=writer,
            )
            assert append_jsonl(
                data / "signals" / f"{symbol}.jsonl",
                _signal(symbol),
                batch_id="2026-09-23:OPEN",
                writer=writer,
            )

    assert connect_count == 1
    assert schema_count == 1
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0] == 50
        assert conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0] == 50
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"


def test_standalone_writer_initializes_and_opens_only_one_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = tmp_path / "data" / "persistence" / "app.sqlite"
    count = 0
    original = db_module._connect

    def counted(path: Path) -> sqlite3.Connection:
        nonlocal count
        count += 1
        return original(path)

    monkeypatch.setattr(db_module, "_connect", counted)
    assert persist_payload(db, "predictions", _prediction("AAPL"))
    assert count == 1


def test_batch_writer_repairs_duplicate_jsonl_after_mirror_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = tmp_path / "data"
    db = data / "persistence" / "app.sqlite"
    path = data / "predictions" / "AAPL.jsonl"
    payload = _prediction("AAPL")

    with pytest.raises(RuntimeError, match="injected mirror failure"):
        with PersistenceWriter(db) as writer:
            def fail(_stream: str, _payload: dict) -> bool:
                raise RuntimeError("injected mirror failure")

            monkeypatch.setattr(writer, "persist", fail)
            append_jsonl(path, payload, batch_id="2026-09-23:OPEN", writer=writer)

    assert len(path.read_text(encoding="utf-8").splitlines()) == 1
    with PersistenceWriter(db) as writer:
        assert not append_jsonl(
            path, payload, batch_id="2026-09-23:OPEN", writer=writer
        )
        assert not append_jsonl(
            path, payload, batch_id="2026-09-23:OPEN", writer=writer
        )
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0] == 1


def test_batch_writer_commits_successful_mirrors_on_later_failure(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    db = data / "persistence" / "app.sqlite"
    with pytest.raises(ValueError, match="stop"):
        with PersistenceWriter(db) as writer:
            assert append_jsonl(
                data / "predictions" / "AAPL.jsonl",
                _prediction("AAPL"),
                batch_id="2026-09-23:OPEN",
                writer=writer,
            )
            raise ValueError("stop")
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0] == 1


def test_wrong_batch_writer_fails_without_writing_durable_jsonl(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    other = tmp_path / "other" / "data"
    path = data / "predictions" / "AAPL.jsonl"
    with PersistenceWriter(other / "persistence" / "app.sqlite") as writer:
        with pytest.raises(ValueError, match="does not match"):
            append_jsonl(path, _prediction("AAPL"), batch_id="2026-09-23:OPEN", writer=writer)
    assert not path.exists()


def test_hydration_replays_all_three_ledgers_with_one_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = tmp_path / "data"
    predictions = data / "predictions" / "AAPL.jsonl"
    signals = data / "signals" / "AAPL.jsonl"
    recs = data / "predictions" / "reconciliations" / "AAPL.jsonl"
    for path in (predictions, signals, recs):
        path.parent.mkdir(parents=True, exist_ok=True)

    prediction = _prediction("AAPL")
    signal = _signal("AAPL")
    rec = {
        "prediction_id": 123456789,
        "prediction_key": "1234567890abcdef",
        "symbol": "AAPL",
        "horizon": "d1",
        "predicted_as_of": prediction["as_of"],
        "resolved_at": "2026-09-24T20:00:00+00:00",
    }
    predictions.write_text(json.dumps(prediction) + "\n", encoding="utf-8")
    signals.write_text(json.dumps(signal) + "\n", encoding="utf-8")
    recs.write_text(json.dumps(rec) + "\n", encoding="utf-8")

    connections = 0
    real_connect = db_module._connect

    def counted(path: Path) -> sqlite3.Connection:
        nonlocal connections
        connections += 1
        return real_connect(path)

    monkeypatch.setattr(db_module, "_connect", counted)
    result = hydrate_persistence_from_jsonl(data)
    assert result["prediction_rows_inserted"] == 1
    assert result["signal_rows_inserted"] == 1
    assert result["reconciliation_rows_inserted"] == 1
    assert connections == 1
    with sqlite3.connect(data / "persistence" / "app.sqlite") as conn:
        assert conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM prediction_reconciliations").fetchone()[0] == 1


def test_batch_writer_rejects_use_outside_context(tmp_path: Path) -> None:
    writer = PersistenceWriter(tmp_path / "data" / "persistence" / "app.sqlite")
    with pytest.raises(RuntimeError, match="inside its context"):
        writer.persist("predictions", _prediction("AAPL"))
    with pytest.raises(ValueError, match="nonnegative"):
        PersistenceWriter(tmp_path / "app.sqlite", commit_every=-1)
