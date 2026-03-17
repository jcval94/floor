from __future__ import annotations

import json
import hashlib
import pickle
from datetime import datetime, timedelta, timezone
from pathlib import Path

from floor.config import RuntimeConfig
from floor.pipeline.intraday_cycle import run_intraday_cycle
from floor.persistence_db import stream_count
from storage.market_db import DailyBar, init_market_db, upsert_daily_bars


def _seed_market_db(db_path: Path) -> None:
    init_market_db(db_path)
    bars: list[DailyBar] = []
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    for idx in range(30):
        ts = (start + timedelta(days=idx)).isoformat()
        bars.append(DailyBar(symbol="AAPL", ts_utc=ts, open=100 + idx, high=101 + idx, low=99 + idx, close=100.5 + idx, volume=1_000 + idx))
        bars.append(DailyBar(symbol="SPY", ts_utc=ts, open=400 + idx, high=401 + idx, low=399 + idx, close=400.5 + idx, volume=10_000 + idx))
    upsert_daily_bars(db_path, bars)


def _seed_models(models_dir: Path) -> None:
    models_dir.mkdir(parents=True, exist_ok=True)
    (models_dir / "value_champion.json").write_text(
        json.dumps({"model_name": "m3_value_linear", "version": "v-train", "params": {"weights": {}, "bias": 95.0}, "metrics": {}}),
        encoding="utf-8",
    )
    (models_dir / "timing_champion.json").write_text(
        json.dumps({"model_name": "m3_timing_multiclass", "version": "t-train", "params": {"calibrator_reliability": {}}, "metrics": {}}),
        encoding="utf-8",
    )




def _write_manifest(models_file_dir: Path, task: str, pkl_path: Path) -> None:
    payload = {
        "task": task,
        "format": "pkl",
        "file_name": pkl_path.name,
        "sha256": hashlib.sha256(pkl_path.read_bytes()).hexdigest(),
        "model_version": "test",
    }
    (models_file_dir / f"{task}_champion.manifest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

def test_run_intraday_cycle_uses_trained_champions(tmp_path: Path) -> None:
    root_dir = tmp_path
    data_dir = tmp_path / "data"
    (root_dir / "config").mkdir(parents=True, exist_ok=True)
    (root_dir / "config" / "universe.yaml").write_text("symbols:\n  - AAPL\n", encoding="utf-8")

    _seed_market_db(data_dir / "market" / "market_data.sqlite")
    _seed_models(data_dir / "training" / "models")

    cfg = RuntimeConfig(root_dir=root_dir, data_dir=data_dir, recommendations_csv_url=None, live_trading_enabled=False)
    run_intraday_cycle(event_type="OPEN", symbols=["AAPL"], cfg=cfg)

    pred_path = data_dir / "predictions" / "AAPL.jsonl"
    lines = pred_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 4
    payloads = [json.loads(line) for line in lines]
    assert all("value:v-train" in payload["model_version"] for payload in payloads)
    assert all(payload["model_version"] != "champion-v0" for payload in payloads)
    m3_rows = [payload for payload in payloads if payload["horizon"] == "m3"]
    assert len(m3_rows) == 1
    assert "m3_status" in m3_rows[0]["m3_payload"]

    db_path = data_dir / "persistence" / "app.sqlite"
    assert db_path.exists()
    assert stream_count(db_path, "predictions") == 4
    assert stream_count(db_path, "signals") >= 3


def test_run_intraday_cycle_loads_models_from_models_file_pkls(tmp_path: Path) -> None:
    root_dir = tmp_path
    data_dir = tmp_path / "data"
    (root_dir / "config").mkdir(parents=True, exist_ok=True)
    (root_dir / "config" / "universe.yaml").write_text("symbols:\n  - AAPL\n", encoding="utf-8")

    _seed_market_db(data_dir / "market" / "market_data.sqlite")
    models_file_dir = data_dir / "training" / "models_file"
    models_file_dir.mkdir(parents=True, exist_ok=True)
    value_pkl = models_file_dir / "value_champion.pkl"
    with value_pkl.open("wb") as fh:
        pickle.dump({"model_name": "m3_value_linear", "version": "v-pkl", "params": {"weights": {}, "bias": 95.0}, "metrics": {}}, fh)
    _write_manifest(models_file_dir, "value", value_pkl)

    timing_pkl = models_file_dir / "timing_champion.pkl"
    with timing_pkl.open("wb") as fh:
        pickle.dump({"model_name": "m3_timing_multiclass", "version": "t-pkl", "params": {"calibrator_reliability": {}}, "metrics": {}}, fh)
    _write_manifest(models_file_dir, "timing", timing_pkl)

    cfg = RuntimeConfig(root_dir=root_dir, data_dir=data_dir, recommendations_csv_url=None, live_trading_enabled=False)
    run_intraday_cycle(event_type="OPEN", symbols=["AAPL"], cfg=cfg)

    pred_path = data_dir / "predictions" / "AAPL.jsonl"
    payloads = [json.loads(line) for line in pred_path.read_text(encoding="utf-8").strip().splitlines()]
    assert payloads
    assert all("value:v-pkl|timing:t-pkl" == payload["model_version"] for payload in payloads)


def test_run_intraday_cycle_fallbacks_to_json_when_manifest_is_invalid(tmp_path: Path) -> None:
    root_dir = tmp_path
    data_dir = tmp_path / "data"
    (root_dir / "config").mkdir(parents=True, exist_ok=True)
    (root_dir / "config" / "universe.yaml").write_text("symbols:\n  - AAPL\n", encoding="utf-8")

    _seed_market_db(data_dir / "market" / "market_data.sqlite")
    _seed_models(data_dir / "training" / "models")

    models_file_dir = data_dir / "training" / "models_file"
    models_file_dir.mkdir(parents=True, exist_ok=True)
    value_pkl = models_file_dir / "value_champion.pkl"
    with value_pkl.open("wb") as fh:
        pickle.dump({"model_name": "m3_value_linear", "version": "v-pkl-bad", "params": {"weights": {}, "bias": 95.0}, "metrics": {}}, fh)
    (models_file_dir / "value_champion.manifest.json").write_text(
        json.dumps({"task": "value", "sha256": "deadbeef"}, ensure_ascii=False),
        encoding="utf-8",
    )

    timing_pkl = models_file_dir / "timing_champion.pkl"
    with timing_pkl.open("wb") as fh:
        pickle.dump({"model_name": "m3_timing_multiclass", "version": "t-pkl-bad", "params": {"calibrator_reliability": {}}, "metrics": {}}, fh)
    (models_file_dir / "timing_champion.manifest.json").write_text(
        json.dumps({"task": "timing", "sha256": "deadbeef"}, ensure_ascii=False),
        encoding="utf-8",
    )

    cfg = RuntimeConfig(root_dir=root_dir, data_dir=data_dir, recommendations_csv_url=None, live_trading_enabled=False)
    run_intraday_cycle(event_type="OPEN", symbols=["AAPL"], cfg=cfg)

    pred_path = data_dir / "predictions" / "AAPL.jsonl"
    payloads = [json.loads(line) for line in pred_path.read_text(encoding="utf-8").strip().splitlines()]
    assert payloads
    assert all("value:v-train|timing:t-train" == payload["model_version"] for payload in payloads)
