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
    (models_dir / "d1_champion.json").write_text(
        json.dumps({"model_name": "d1_heuristic_v1", "version": "d1-train", "params": {}, "metrics": {}}),
        encoding="utf-8",
    )
    (models_dir / "w1_champion.json").write_text(
        json.dumps({"model_name": "w1_heuristic_v1", "version": "w1-train", "params": {}, "metrics": {}}),
        encoding="utf-8",
    )
    (models_dir / "q1_champion.json").write_text(
        json.dumps({"model_name": "q1_heuristic_v1", "version": "q1-train", "params": {}, "metrics": {}}),
        encoding="utf-8",
    )
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
    assert all("value:v-train" in payload["model_version"] and "d1:d1-train" in payload["model_version"] for payload in payloads)
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
    for task in ("d1", "w1", "q1"):
        pkl = models_file_dir / f"{task}_champion.pkl"
        with pkl.open("wb") as fh:
            pickle.dump({"model_name": f"{task}_heuristic_v1", "version": f"{task}-pkl", "params": {}, "metrics": {}}, fh)
        _write_manifest(models_file_dir, task, pkl)

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
    assert all("d1:d1-pkl|w1:w1-pkl|q1:q1-pkl|value:v-pkl|timing:t-pkl" == payload["model_version"] for payload in payloads)


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
    assert all("d1:d1-train|w1:w1-train|q1:q1-train|value:v-train|timing:t-train" == payload["model_version"] for payload in payloads)


def test_signal_from_prediction_buy_sell_hold_rules() -> None:
    from floor.pipeline.intraday_cycle import _signal_from_prediction

    buy_signal = _signal_from_prediction(
        symbol="AAPL",
        horizon="d1",
        floor=100.0,
        ceiling=104.0,
        expected_return=0.03,
        confidence_score=0.8,
        composite_signal_score=None,
    )
    assert buy_signal.action == "BUY"

    sell_signal = _signal_from_prediction(
        symbol="AAPL",
        horizon="d1",
        floor=100.0,
        ceiling=104.0,
        expected_return=-0.03,
        confidence_score=0.8,
        composite_signal_score=None,
    )
    assert sell_signal.action == "SELL"

    neutral_signal = _signal_from_prediction(
        symbol="AAPL",
        horizon="d1",
        floor=100.0,
        ceiling=104.0,
        expected_return=0.0,
        confidence_score=0.8,
        composite_signal_score=None,
    )
    assert neutral_signal.action == "HOLD"


def test_signal_from_prediction_holds_on_low_confidence() -> None:
    from floor.pipeline.intraday_cycle import _signal_from_prediction

    low_conf_signal = _signal_from_prediction(
        symbol="AAPL",
        horizon="d1",
        floor=100.0,
        ceiling=100.4,
        expected_return=0.05,
        confidence_score=0.2,
        composite_signal_score=0.1,
    )

    assert low_conf_signal.action == "HOLD"


def test_run_intraday_cycle_external_override_has_priority_and_rationale(tmp_path: Path, monkeypatch) -> None:
    from floor.external.google_sheets import ExternalRecommendation

    root_dir = tmp_path
    data_dir = tmp_path / "data"
    (root_dir / "config").mkdir(parents=True, exist_ok=True)
    (root_dir / "config" / "universe.yaml").write_text("symbols:\n  - AAPL\n", encoding="utf-8")

    _seed_market_db(data_dir / "market" / "market_data.sqlite")
    _seed_models(data_dir / "training" / "models")

    monkeypatch.setattr(
        "floor.pipeline.intraday_cycle.fetch_recommendations",
        lambda _url: [ExternalRecommendation(symbol="AAPL", action="SELL", confidence=0.9, note="risk desk")],
    )

    cfg = RuntimeConfig(root_dir=root_dir, data_dir=data_dir, recommendations_csv_url="http://mock", live_trading_enabled=False)
    run_intraday_cycle(event_type="OPEN", symbols=["AAPL"], cfg=cfg)

    signal_path = data_dir / "signals" / "AAPL.jsonl"
    signals = [json.loads(line) for line in signal_path.read_text(encoding="utf-8").strip().splitlines()]
    assert signals
    assert all(signal["action"] == "SELL" for signal in signals)
    assert all("external_override=SELL" in signal["rationale"] for signal in signals)
    assert all("model_action=" in signal["rationale"] for signal in signals)


def test_run_intraday_cycle_persists_neutral_fallback_when_champions_missing(tmp_path: Path) -> None:
    root_dir = tmp_path
    data_dir = tmp_path / "data"
    (root_dir / "config").mkdir(parents=True, exist_ok=True)
    (root_dir / "config" / "universe.yaml").write_text("symbols:\n  - AAPL\n", encoding="utf-8")

    _seed_market_db(data_dir / "market" / "market_data.sqlite")

    cfg = RuntimeConfig(root_dir=root_dir, data_dir=data_dir, recommendations_csv_url=None, live_trading_enabled=False)
    run_intraday_cycle(event_type="OPEN", symbols=["AAPL"], cfg=cfg)

    pred_path = data_dir / "predictions" / "AAPL.jsonl"
    payloads = [json.loads(line) for line in pred_path.read_text(encoding="utf-8").strip().splitlines()]
    assert len(payloads) == 4
    assert all(payload["model_version"] == "d1:unknown|w1:unknown|q1:unknown|value:unknown|timing:unknown" for payload in payloads)
    assert all(payload["m3_status"] == "unavailable" for payload in payloads)

    signal_path = data_dir / "signals" / "AAPL.jsonl"
    signals = [json.loads(line) for line in signal_path.read_text(encoding="utf-8").strip().splitlines()]
    assert len(signals) == 3
    assert all(signal["action"] == "HOLD" for signal in signals)


def test_prediction_payloads_use_horizon_specific_model_confidence() -> None:
    from floor.pipeline.intraday_cycle import _prediction_payloads

    row = {
        "floor_d1": 95.0,
        "ceiling_d1": 105.0,
        "floor_time_bucket_d1": "OPEN_PLUS_2H",
        "ceiling_time_bucket_d1": "CLOSE",
        "expected_return_d1": 0.01,
        "expected_range_d1": 10.0,
        "breach_prob_d1": 0.2,
        "floor_w1": 90.0,
        "ceiling_w1": 110.0,
        "floor_day_w1": 2,
        "ceiling_day_w1": 5,
        "expected_return_w1": 0.02,
        "expected_range_w1": 20.0,
        "breach_prob_w1": 0.35,
        "floor_q1": 80.0,
        "ceiling_q1": 120.0,
        "floor_day_q1": 10,
        "ceiling_day_q1": 40,
        "expected_return_q1": 0.03,
        "expected_range_q1": 40.0,
        "breach_prob_q1": 0.5,
        "confidence_score": 0.99,
        "floor_m3": None,
        "floor_week_m3": None,
        "floor_week_m3_confidence": None,
        "expected_return_m3": None,
        "expected_range_m3": None,
        "m3_status": "blocked",
        "m3_block_reason": "missing",
    }

    payloads = dict(_prediction_payloads(row, event_type="OPEN"))
    assert payloads["d1"]["confidence_score"] == 0.8
    assert payloads["w1"]["confidence_score"] == 0.65
    assert payloads["q1"]["confidence_score"] == 0.5
    assert payloads["d1"]["floor_time_probability"] == 0.8
    assert payloads["w1"]["ceiling_time_probability"] == 0.65
