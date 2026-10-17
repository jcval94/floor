from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from floor.persistence_db import latest_predictions
from league import market_features


def test_historical_latest_predictions_never_read_newer_batches(tmp_path: Path) -> None:
    db = tmp_path / "persistence.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE predictions (id INTEGER PRIMARY KEY, symbol TEXT, "
            "horizon TEXT, as_of TEXT, payload_json TEXT)"
        )
        for index, day in enumerate(["2026-10-14", "2026-10-16", "2026-10-15"], 1):
            payload = {"symbol": "AAA", "horizon": "d1", "as_of": day, "marker": day}
            conn.execute(
                "INSERT INTO predictions (id,symbol,horizon,as_of,payload_json) "
                "VALUES (?,?,?,?,?)",
                (index, "AAA", "d1", day, json.dumps(payload)),
            )
    current = latest_predictions(db)
    historic = latest_predictions(db, as_of_through_session="2026-10-15")
    assert current[0]["as_of"] == "2026-10-15"  # Preserve existing insertion semantics
    assert historic[0]["as_of"] == "2026-10-15"
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM predictions WHERE id=3")
    historic = latest_predictions(db, as_of_through_session="2026-10-15")
    assert historic[0]["as_of"] == "2026-10-14"
    assert latest_predictions(db)[0]["as_of"] == "2026-10-16"


def test_historical_market_snapshot_does_not_read_future_daily_bars(
    tmp_path: Path, monkeypatch,
) -> None:
    bars = [
        {"symbol": "SPY", "timestamp": "2026-10-14T13:30:00+00:00"},
        {"symbol": "SPY", "timestamp": "2026-10-15T13:30:00+00:00"},
        {"symbol": "SPY", "timestamp": "2026-10-16T13:30:00+00:00"},
        {"symbol": "AAA", "timestamp": "2026-10-16T13:30:00+00:00"},
    ]
    kwargs = []
    monkeypatch.setattr(market_features, "load_daily_bars", lambda *args: bars)
    monkeypatch.setattr(
        market_features, "latest_predictions",
        lambda *args, **kw: (kwargs.append(kw) or []),
    )
    snap = market_features.build_league_market_snapshot(
        tmp_path / "market.db", tmp_path / "predictions.db", ["AAA"],
        required_market_session="2026-10-15",
    )
    assert snap["session"] == "2026-10-15"
    assert snap["status"] == "INCOMPLETE_FORECAST_INPUT"
    assert kwargs == [{"as_of_through_session": "2026-10-15"}]
    assert snap["bars"] == {}
    normal = market_features.build_league_market_snapshot(
        tmp_path / "market.db", tmp_path / "predictions.db", ["AAA"],
    )
    assert normal["session"] == "2026-10-16"
