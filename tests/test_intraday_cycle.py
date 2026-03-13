from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from floor.config import RuntimeConfig
from floor.pipeline.intraday_cycle import run_intraday_cycle
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
    assert len(lines) == 3
    payloads = [json.loads(line) for line in lines]
    assert all("value:m3_value_linear@v-train" in payload["model_version"] for payload in payloads)
    assert all(payload["model_version"] != "champion-v0" for payload in payloads)
    assert (data_dir / "persistence" / "app.sqlite").exists()
