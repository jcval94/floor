from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import floor.storage as storage_module
from features.model_competition import build_model_specs
from floor.persistence_db import stream_count
from floor.persistence_hydration import hydrate_persistence_from_jsonl
from floor.prediction_reconciliation import reconcile_predictions
from floor.storage import append_jsonl, load_jsonl_rows
from models.train_classic_horizons import _PreparedRow, _metrics, train_horizon_competition
from storage.market_db import DailyBar, init_market_db, upsert_daily_bars


def _seed_market(db_path: Path, sessions: int = 8) -> None:
    init_market_db(db_path)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    bars = [
        DailyBar(
            symbol="AAPL",
            ts_utc=(start + timedelta(days=idx)).isoformat(),
            open=100 + idx,
            high=101 + idx,
            low=99 + idx,
            close=100 + idx,
            volume=1_000_000,
        )
        for idx in range(sessions)
    ]
    upsert_daily_bars(db_path, bars)


def test_truthful_classic_model_names_do_not_claim_unimplemented_algorithms() -> None:
    specs = build_model_specs()
    families = {spec.model_family for spec in specs}
    ids = {spec.model_id for spec in specs}
    assert families == {
        "regime_median",
        "boosted_stumps",
        "sequence_linear",
        "regularized_linear",
    }
    forbidden = ("xgboost", "lstm", "evt_cp", "qenet")
    assert not any(token in model_id for token in forbidden for model_id in ids)


def test_explicit_training_splits_refuse_test_as_champion_validation() -> None:
    row = {
        "timestamp": "2026-01-01T00:00:00+00:00",
        "symbol": "AAA",
        "close": 100.0,
        "floor_d1": 98.0,
        "ceiling_d1": 103.0,
        "split_eligible_d1": True,
    }
    rows = [
        {**row, "split": "train"},
        {**row, "timestamp": "2026-01-02T00:00:00+00:00", "split": "test"},
    ]
    with pytest.raises(ValueError, match="refuses train/test fallback"):
        train_horizon_competition(rows, "d1", "vtest")


def test_classic_coverage_metrics_measure_real_boundary_breaches() -> None:
    item = _PreparedRow(
        row={},
        close=100.0,
        floor_delta=0.05,
        ceiling_delta=0.05,
        features={},
    )
    covered = _metrics([item], [0.06], [0.06])
    breached = _metrics([item], [0.04], [0.04])

    assert covered["test_interval_coverage"] == 1.0
    assert covered["empirical_breach_rate"] == 0.0
    assert breached["test_interval_coverage"] == 0.0
    assert breached["empirical_breach_rate"] == 1.0


def test_batch_id_makes_jsonl_and_sqlite_prediction_write_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "data" / "predictions" / "AAPL.jsonl"
    payload = {
        "symbol": "AAPL",
        "as_of": "2026-01-02T14:30:00-05:00",
        "event_type": "OPEN",
        "horizon": "d1",
        "floor_value": 99.0,
        "ceiling_value": 102.0,
        "model_version": "v2",
    }
    assert append_jsonl(path, payload, batch_id="2026-01-02:OPEN") is True
    assert append_jsonl(path, payload, batch_id="2026-01-02:OPEN") is False
    assert len(load_jsonl_rows(path)) == 1
    assert stream_count(tmp_path / "data" / "persistence" / "app.sqlite", "predictions") == 1


def test_durable_jsonl_survives_sqlite_cache_failure_and_repairs_on_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "data" / "predictions" / "AAPL.jsonl"
    payload = {
        "symbol": "AAPL",
        "as_of": "2026-01-02T14:30:00-05:00",
        "event_type": "OPEN",
        "horizon": "d1",
        "floor_value": 99.0,
        "ceiling_value": 102.0,
        "model_version": "v2",
    }

    def fail_sqlite(*args: object, **kwargs: object) -> bool:
        raise RuntimeError("simulated sqlite failure")

    monkeypatch.setattr(storage_module, "persist_payload", fail_sqlite)
    with pytest.raises(RuntimeError, match="simulated sqlite failure"):
        storage_module.append_jsonl(path, payload, batch_id="2026-01-02:OPEN")

    # The durable ledger was written first even though the cache failed.
    rows = load_jsonl_rows(path)
    assert len(rows) == 1
    assert rows[0]["batch_id"] == "2026-01-02:OPEN"

    # A retry must not duplicate JSONL and must repair the reconstructable cache.
    monkeypatch.undo()
    assert storage_module.append_jsonl(path, payload, batch_id="2026-01-02:OPEN") is False
    assert len(load_jsonl_rows(path)) == 1
    assert stream_count(tmp_path / "data" / "persistence" / "app.sqlite", "predictions") == 1


def test_sqlite_cache_can_be_rebuilt_from_durable_jsonl(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    path = data_dir / "predictions" / "AAPL.jsonl"
    append_jsonl(
        path,
        {
            "symbol": "AAPL",
            "as_of": "2026-01-02T14:30:00-05:00",
            "event_type": "OPEN",
            "horizon": "w1",
            "floor_value": 95.0,
            "ceiling_value": 105.0,
            "model_version": "v2",
        },
        batch_id="2026-01-02:OPEN",
    )
    db_path = data_dir / "persistence" / "app.sqlite"
    db_path.unlink()

    result = hydrate_persistence_from_jsonl(data_dir)
    assert result["prediction_rows_seen"] == 1
    assert result["prediction_rows_inserted"] == 1
    assert stream_count(db_path, "predictions") == 1


def test_reconciliation_evidence_survives_fresh_sqlite_runner(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _seed_market(data_dir / "market" / "market_data.sqlite", sessions=8)
    append_jsonl(
        data_dir / "predictions" / "AAPL.jsonl",
        {
            "symbol": "AAPL",
            "as_of": "2026-01-01T12:00:00+00:00",
            "event_type": "OPEN",
            "horizon": "d1",
            "floor_value": 98.0,
            "ceiling_value": 102.0,
            "model_version": "v2",
        },
        batch_id="2026-01-01:OPEN",
    )
    first = reconcile_predictions(data_dir)
    assert first["reconciled"] == 1
    evidence = data_dir / "predictions" / "reconciliations" / "AAPL.jsonl"
    assert len(load_jsonl_rows(evidence)) == 1

    db_path = data_dir / "persistence" / "app.sqlite"
    db_path.unlink()
    hydrate_persistence_from_jsonl(data_dir)
    second = reconcile_predictions(data_dir)
    assert second["pending"] == 0
    assert second["reconciled"] == 0
    assert len(load_jsonl_rows(evidence)) == 1
