from __future__ import annotations

from pathlib import Path

from features.build_training_from_db import build_rows_from_db
from storage.market_db import DailyBar, init_market_db, load_daily_bars, upsert_daily_bars
from storage.yahoo_ingest import parse_daily_bars


def test_market_db_upsert_and_load(tmp_path: Path) -> None:
    db = tmp_path / "market.sqlite"
    init_market_db(db)
    upsert_daily_bars(
        db,
        [
            DailyBar(symbol="AAPL", ts_utc="2025-01-02T00:00:00+00:00", open=1, high=2, low=0.5, close=1.5, volume=10),
            DailyBar(symbol="SPY", ts_utc="2025-01-02T00:00:00+00:00", open=10, high=20, low=9, close=15, volume=100),
        ],
    )

    rows = load_daily_bars(db, ["AAPL", "SPY"])
    assert len(rows) == 2
    assert {r["symbol"] for r in rows} == {"AAPL", "SPY"}


def test_parse_daily_bars_filters_missing() -> None:
    payload = {
        "chart": {
            "result": [
                {
                    "timestamp": [1735776000, 1735862400],
                    "indicators": {
                        "quote": [
                            {
                                "open": [1.0, None],
                                "high": [2.0, 2.2],
                                "low": [0.9, 1.1],
                                "close": [1.8, 2.0],
                                "volume": [1000, 1100],
                            }
                        ]
                    },
                }
            ]
        }
    }
    bars = parse_daily_bars("AAPL", payload)
    assert len(bars) == 1
    assert bars[0].symbol == "AAPL"


def test_build_rows_from_db(tmp_path: Path) -> None:
    db = tmp_path / "market.sqlite"
    init_market_db(db)
    upsert_daily_bars(
        db,
        [
            DailyBar(symbol="AAPL", ts_utc="2025-01-02T00:00:00+00:00", open=1, high=2, low=0.5, close=1.5, volume=10),
            DailyBar(symbol="SPY", ts_utc="2025-01-02T00:00:00+00:00", open=10, high=20, low=9, close=15, volume=100),
        ],
    )

    universe = tmp_path / "universe.yaml"
    universe.write_text("symbols:\n  - AAPL\n", encoding="utf-8")

    rows = build_rows_from_db(db, universe)
    assert len(rows) == 1
    assert rows[0]["benchmark_close"] == 15.0
    assert rows[0]["symbol"] == "AAPL"
