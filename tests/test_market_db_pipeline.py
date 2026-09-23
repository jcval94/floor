from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

from features.build_training_from_db import build_rows_from_db
from features.feature_builder import build_features
from floor.config import RuntimeConfig
from floor.pipeline.prediction_runtime import _latest_feature_rows
from storage.market_db import (
    DailyBar,
    init_market_db,
    load_daily_bars,
    load_recent_daily_bars,
    upsert_daily_bars,
)
from storage.yahoo_ingest import _symbol_to_yahoo, parse_daily_bars


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


def test_build_rows_from_db_aligns_benchmark_by_session_date(tmp_path: Path) -> None:
    db = tmp_path / "market.sqlite"
    init_market_db(db)
    upsert_daily_bars(
        db,
        [
            DailyBar(symbol="AAPL", ts_utc="2025-01-02T20:03:23+00:00", open=1, high=2, low=0.5, close=1.5, volume=10),
            DailyBar(symbol="SPY", ts_utc="2025-01-02T20:00:00+00:00", open=10, high=20, low=9, close=15, volume=100),
        ],
    )

    universe = tmp_path / "universe.yaml"
    universe.write_text("symbols:\n  - AAPL\n", encoding="utf-8")

    rows = build_rows_from_db(db, universe)
    assert len(rows) == 1
    assert rows[0]["benchmark_close"] == 15.0


def test_symbol_to_yahoo_maps_dot_ticker() -> None:
    assert _symbol_to_yahoo("BRK.B") == "BRK-B"
    assert _symbol_to_yahoo("AAPL") == "AAPL"


def test_recent_market_loader_bounds_each_symbol_before_cutoff(tmp_path: Path) -> None:
    db = tmp_path / "market.sqlite"
    init_market_db(db)
    bars: list[DailyBar] = []
    start = datetime(2025, 1, 1)
    for idx in range(5):
        ts = (start + timedelta(days=idx)).isoformat() + "+00:00"
        bars.extend(
            [
                DailyBar(
                    symbol="AAPL",
                    ts_utc=ts,
                    open=100 + idx,
                    high=101 + idx,
                    low=99 + idx,
                    close=100.5 + idx,
                    volume=1_000 + idx,
                ),
                DailyBar(
                    symbol="SPY",
                    ts_utc=ts,
                    open=400 + idx,
                    high=401 + idx,
                    low=399 + idx,
                    close=400.5 + idx,
                    volume=10_000 + idx,
                ),
            ]
        )
    upsert_daily_bars(db, bars)

    rows = load_recent_daily_bars(
        db,
        ["AAPL", "SPY"],
        limit_per_symbol=2,
        max_session=date(2025, 1, 4),
    )

    by_symbol = {
        symbol: [row["timestamp"][:10] for row in rows if row["symbol"] == symbol]
        for symbol in ("AAPL", "SPY")
    }
    assert by_symbol == {
        "AAPL": ["2025-01-03", "2025-01-04"],
        "SPY": ["2025-01-03", "2025-01-04"],
    }


def test_bounded_serving_features_match_full_history_latest_row(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    db = data_dir / "market" / "market_data.sqlite"
    init_market_db(db)
    universe = tmp_path / "config" / "universe.yaml"
    universe.parent.mkdir(parents=True, exist_ok=True)
    universe.write_text("symbols:\n  - AAPL\n", encoding="utf-8")

    bars: list[DailyBar] = []
    cursor = datetime(2024, 1, 2)
    produced = 0
    while produced < 330:
        if cursor.weekday() < 5:
            aapl = 100.0 + 0.08 * produced + 0.7 * ((produced % 11) - 5) / 10
            spy = 400.0 + 0.05 * produced + 0.4 * ((produced % 7) - 3) / 10
            ts = cursor.replace(hour=20).isoformat() + "+00:00"
            bars.extend(
                [
                    DailyBar(
                        symbol="AAPL",
                        ts_utc=ts,
                        open=aapl - 0.2,
                        high=aapl + 1.1,
                        low=aapl - 0.9,
                        close=aapl,
                        volume=1_000_000 + produced * 100,
                    ),
                    DailyBar(
                        symbol="SPY",
                        ts_utc=ts,
                        open=spy - 0.3,
                        high=spy + 1.4,
                        low=spy - 1.0,
                        close=spy,
                        volume=10_000_000 + produced * 100,
                    ),
                ]
            )
            produced += 1
        cursor += timedelta(days=1)
    upsert_daily_bars(db, bars)

    full_rows = build_rows_from_db(db, universe)
    full_latest = build_features(full_rows)[-1]

    cfg = RuntimeConfig(root_dir=tmp_path, data_dir=data_dir)
    bounded = _latest_feature_rows(cfg, ["AAPL"])
    assert len(bounded) == 1
    assert bounded[0] == full_latest
