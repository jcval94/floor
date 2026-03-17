from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from floor.prediction_reconciliation import reconcile_predictions
from floor.storage import append_jsonl
from storage.market_db import DailyBar, init_market_db, upsert_daily_bars


def _seed_market(db_path: Path, symbol: str = "AAPL", sessions: int = 90) -> None:
    init_market_db(db_path)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    bars: list[DailyBar] = []
    for i in range(sessions):
        day = start + timedelta(days=i)
        bars.append(
            DailyBar(
                symbol=symbol,
                ts_utc=day.isoformat(),
                open=100 + i,
                high=101 + i,
                low=99 + i,
                close=100 + i,
                volume=1_000_000,
            )
        )
    upsert_daily_bars(db_path, bars)


def test_reconcile_predictions_persists_mature_windows(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _seed_market(data_dir / "market" / "market_data.sqlite", sessions=90)

    append_jsonl(
        data_dir / "predictions" / "AAPL.jsonl",
        {
            "symbol": "AAPL",
            "as_of": "2026-01-01T12:00:00+00:00",
            "event_type": "OPEN",
            "horizon": "d1",
            "floor_value": 98.5,
            "ceiling_value": 103.0,
            "model_version": "v1",
        },
    )
    append_jsonl(
        data_dir / "predictions" / "AAPL.jsonl",
        {
            "symbol": "AAPL",
            "as_of": "2026-01-01T12:00:00+00:00",
            "event_type": "OPEN",
            "horizon": "m3",
            "floor_value": None,
            "ceiling_value": None,
            "floor_week_m3": 3,
            "model_version": "v1",
        },
    )

    result = reconcile_predictions(data_dir)
    assert result["pending"] == 2
    assert result["reconciled"] == 2

    db_path = data_dir / "persistence" / "app.sqlite"
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT horizon, window_sessions, realized_floor, realized_ceiling, m3_predicted_week, m3_realized_week FROM prediction_reconciliations ORDER BY id"
        ).fetchall()
    assert len(rows) == 2
    assert rows[0][0] == "d1"
    assert rows[0][1] == 1
    assert rows[1][0] == "m3"
    assert rows[1][1] == 65
    assert rows[1][4] == 3
    assert rows[1][5] >= 1


def test_reconcile_predictions_skips_until_window_is_complete(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _seed_market(data_dir / "market" / "market_data.sqlite", sessions=8)

    append_jsonl(
        data_dir / "predictions" / "AAPL.jsonl",
        {
            "symbol": "AAPL",
            "as_of": "2026-01-01T12:00:00+00:00",
            "event_type": "OPEN",
            "horizon": "q1",
            "floor_value": 98.5,
            "ceiling_value": 103.0,
            "model_version": "v1",
        },
    )

    result = reconcile_predictions(data_dir)
    assert result["pending"] == 1
    assert result["reconciled"] == 0

    db_path = data_dir / "persistence" / "app.sqlite"
    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM prediction_reconciliations").fetchone()[0]
    assert count == 0
