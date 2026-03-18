from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from features.build_training_from_db import build_rows_from_db
from features.run_features import build_modelable_dataset
from forecasting.run_forecast import run_forecast_pipeline
from models.run_training import run_training
from models.sync_models_file import sync_champions
from models.train_classic_horizons import run as run_classic_horizons
from storage.market_db import DailyBar, init_market_db, upsert_daily_bars


def _seed_market_db(db_path: Path) -> None:
    init_market_db(db_path)
    bars: list[DailyBar] = []
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    for idx in range(140):
        ts = (start + timedelta(days=idx)).isoformat()
        aapl = 180.0 + idx * 0.35
        spy = 500.0 + idx * 0.2
        bars.append(DailyBar(symbol="AAPL", ts_utc=ts, open=aapl - 0.5, high=aapl + 1.1, low=aapl - 1.0, close=aapl, volume=1_000_000 + idx))
        bars.append(DailyBar(symbol="SPY", ts_utc=ts, open=spy - 0.4, high=spy + 0.9, low=spy - 0.8, close=spy, volume=5_000_000 + idx))
    upsert_daily_bars(db_path, bars)


def test_end_to_end_etl_training_and_prediction_for_all_model_pipelines(tmp_path: Path) -> None:
    root = tmp_path
    data_dir = tmp_path / "data"
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "universe.yaml").write_text("symbols:\n  - AAPL\n", encoding="utf-8")

    market_db = data_dir / "market" / "market_data.sqlite"
    _seed_market_db(market_db)

    # ETL 1/2: market DB -> raw training rows
    raw_rows = build_rows_from_db(db_path=market_db, universe_path=root / "config" / "universe.yaml")
    assert raw_rows
    assert all(row["symbol"] == "AAPL" for row in raw_rows)

    raw_jsonl = data_dir / "training" / "yahoo_market_rows.jsonl"
    raw_jsonl.parent.mkdir(parents=True, exist_ok=True)
    raw_jsonl.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in raw_rows) + "\n", encoding="utf-8")

    # ETL 2/2: feature/labels ABT
    modelable = build_modelable_dataset(raw_rows)
    modelable_path = data_dir / "training" / "modelable_dataset.json"
    modelable_path.write_text(json.dumps(modelable, ensure_ascii=False), encoding="utf-8")
    assert modelable["rows"]
    assert {"floor_d1", "floor_w1", "floor_q1", "floor_m3", "floor_week_m3"}.issubset(set(modelable["final_model_columns"]))

    models_dir = data_dir / "training" / "models"
    version = "v-e2e"

    # d1/w1/q1 pipeline: classic-horizon competition
    run_classic_horizons(dataset_path=modelable_path, output_dir=models_dir, version=version)
    for task in ("d1", "w1", "q1"):
        champion = json.loads((models_dir / f"{task}_champion.json").read_text(encoding="utf-8"))
        assert champion["model_name"].startswith(("evt_cp_", "xgboost_", "lstm_", "qenet_"))

    # m3 pipeline: value + timing
    run_training(
        dataset_path=modelable_path,
        output_dir=data_dir / "training",
        version=version,
        tasks="value,timing",
        training_mode="manual",
        persistence_db_path=data_dir / "persistence" / "app.sqlite",
    )
    assert (models_dir / "value_champion.json").exists()
    assert (models_dir / "timing_champion.json").exists()

    # Unified persistence format used by intraday loader
    sync_champions(models_dir=models_dir, models_file_dir=data_dir / "training" / "models_file", tasks=["d1", "w1", "q1", "value", "timing"])
    for task in ("d1", "w1", "q1", "value", "timing"):
        assert (data_dir / "training" / "models_file" / f"{task}_champion.pkl").exists()
        assert (data_dir / "training" / "models_file" / f"{task}_champion.manifest.json").exists()

    # Prediction check using the latest ABT row
    featured_rows = modelable["rows"]
    latest_row = max(featured_rows, key=lambda r: str(r.get("timestamp", "")))
    prediction_out = run_forecast_pipeline(
        market_rows=[latest_row],
        ai_by_symbol={"AAPL": {"ai_consensus_score": 0.4, "ai_conviction": 0.7}},
        session="OPEN",
        as_of=datetime(2026, 3, 18, tzinfo=timezone.utc),
        model_registry_dir=models_dir,
    )
    assert len(prediction_out["dataset_forecasts"]) == 1
    forecast = prediction_out["dataset_forecasts"][0]
    for col in (
        "floor_d1",
        "ceiling_d1",
        "floor_w1",
        "ceiling_w1",
        "floor_q1",
        "ceiling_q1",
        "floor_m3",
        "floor_week_m3",
    ):
        assert forecast.get(col) is not None
