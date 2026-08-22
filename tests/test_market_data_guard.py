from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from utils.market_data_guard import validate_market_data_freshness


def _seed_db(path: Path, rows: list[tuple[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE daily_bars (
                symbol TEXT NOT NULL,
                ts_utc TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                source TEXT NOT NULL DEFAULT 'test',
                fetched_at_utc TEXT NOT NULL DEFAULT '',
                raw_payload TEXT,
                PRIMARY KEY (symbol, ts_utc)
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO daily_bars(symbol, ts_utc, open, high, low, close, volume)
            VALUES(?, ?, 1, 1, 1, 1, 1)
            """,
            rows,
        )


def test_fresh_data_passes(tmp_path: Path) -> None:
    now = datetime(2026, 8, 21, 18, 0, tzinfo=timezone.utc)
    db = tmp_path / "market.sqlite"
    fresh = (now - timedelta(days=1)).isoformat()
    _seed_db(db, [("AAPL", fresh), ("SPY", fresh)])

    result = validate_market_data_freshness(db, ["AAPL", "SPY"], now=now, max_age_days=7)

    assert result["status"] == "OK"
    assert result["symbols_present"] == 2


def test_stale_symbol_blocks_inference(tmp_path: Path) -> None:
    now = datetime(2026, 8, 21, 18, 0, tzinfo=timezone.utc)
    db = tmp_path / "market.sqlite"
    _seed_db(db, [("AAPL", (now - timedelta(days=8)).isoformat())])

    with pytest.raises(RuntimeError, match="stale=AAPL:8d"):
        validate_market_data_freshness(db, ["AAPL"], now=now, max_age_days=7)


def test_missing_symbol_blocks_inference(tmp_path: Path) -> None:
    now = datetime(2026, 8, 21, 18, 0, tzinfo=timezone.utc)
    db = tmp_path / "market.sqlite"
    _seed_db(db, [("AAPL", now.isoformat())])

    with pytest.raises(RuntimeError, match="missing=SPY"):
        validate_market_data_freshness(db, ["AAPL", "SPY"], now=now)


def test_future_timestamp_blocks_inference(tmp_path: Path) -> None:
    now = datetime(2026, 8, 21, 18, 0, tzinfo=timezone.utc)
    db = tmp_path / "market.sqlite"
    _seed_db(db, [("AAPL", (now + timedelta(days=1)).isoformat())])

    with pytest.raises(RuntimeError, match="future=AAPL"):
        validate_market_data_freshness(db, ["AAPL"], now=now)


def test_missing_db_blocks_inference(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="DB missing"):
        validate_market_data_freshness(tmp_path / "missing.sqlite", ["AAPL"])
