from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from floor.config import RuntimeConfig
from floor.pipeline.intraday_cycle import run_intraday_cycle
from floor.reporting.generate_site_data import build_dashboard_snapshot
from storage.market_db import DailyBar, init_market_db, upsert_daily_bars
from utils.pages_build import build_pages_data


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
    for task, version in (("d1", "d1-train"), ("w1", "w1-train"), ("q1", "q1-train")):
        (models_dir / f"{task}_champion.json").write_text(
            json.dumps({"model_name": f"{task}_heuristic_v1", "version": version, "params": {}, "metrics": {}}),
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


def test_m3_contract_flows_to_site_data(tmp_path: Path) -> None:
    root_dir = tmp_path
    data_dir = tmp_path / "data"
    (root_dir / "config").mkdir(parents=True, exist_ok=True)
    (root_dir / "config" / "universe.yaml").write_text("symbols:\n  - AAPL\n", encoding="utf-8")

    _seed_market_db(data_dir / "market" / "market_data.sqlite")
    _seed_models(data_dir / "training" / "models")

    cfg = RuntimeConfig(root_dir=root_dir, data_dir=data_dir, recommendations_csv_url=None, live_trading_enabled=False)
    run_intraday_cycle(event_type="OPEN", symbols=["AAPL"], cfg=cfg)
    build_dashboard_snapshot(data_dir, data_dir / "reports" / "dashboard.json")

    site_data_dir = tmp_path / "site" / "data"
    build_pages_data(data_dir=data_dir, site_data_dir=site_data_dir, universe_path=root_dir / "config" / "universe.yaml")

    dashboard = json.loads((data_dir / "reports" / "dashboard.json").read_text(encoding="utf-8"))
    assert dashboard["prediction_contract"]["horizons"] == ["d1", "w1", "q1", "m3"]

    forecasts_payload = json.loads((site_data_dir / "forecasts.json").read_text(encoding="utf-8"))
    assert forecasts_payload["contract"]["horizons"] == ["d1", "w1", "q1", "m3"]

    forecasts = forecasts_payload["rows"]
    m3_rows = [row for row in forecasts if row.get("horizon") == "m3"]
    assert len(m3_rows) == 1

    d1_row = next(row for row in forecasts if row.get("horizon") == "d1")
    # m3 must survive as flattened fields, not only nested payload, for all horizons.
    assert "floor_m3" in d1_row
    assert "floor_week_m3" in d1_row
    assert "m3_status" in d1_row
    assert d1_row["m3_status"] == "ok"
    assert d1_row.get("m3_block_reason") is None

    m3_payload = m3_rows[0].get("m3_payload", {})
    assert "m3_status" in m3_payload
    assert "floor_week_m3" in m3_payload
