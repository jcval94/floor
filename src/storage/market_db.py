from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class DailyBar:
    symbol: str
    ts_utc: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    source: str = "yahoo"


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_bars (
                symbol TEXT NOT NULL,
                ts_utc TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                source TEXT NOT NULL DEFAULT 'yahoo',
                fetched_at_utc TEXT NOT NULL,
                raw_payload TEXT,
                PRIMARY KEY (symbol, ts_utc)
            )
            """
        )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_daily_bars_symbol_ts
        ON daily_bars(symbol, ts_utc)
        """
    )


def init_market_db(db_path: Path) -> None:
    with _connect(db_path) as conn:
        _ensure_schema(conn)


def upsert_daily_bars(db_path: Path, bars: list[DailyBar], raw_payload: dict | None = None) -> int:
    if not bars:
        return 0

    fetched_at = datetime.now(tz=timezone.utc).isoformat()
    raw = json.dumps(raw_payload, ensure_ascii=False) if raw_payload else None

    with _connect(db_path) as conn:
        _ensure_schema(conn)
        cur = conn.executemany(
            """
            INSERT INTO daily_bars(symbol, ts_utc, open, high, low, close, volume, source, fetched_at_utc, raw_payload)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, ts_utc) DO UPDATE SET
                open=excluded.open,
                high=excluded.high,
                low=excluded.low,
                close=excluded.close,
                volume=excluded.volume,
                source=excluded.source,
                fetched_at_utc=excluded.fetched_at_utc,
                raw_payload=excluded.raw_payload
            """,
            [
                (
                    b.symbol,
                    b.ts_utc,
                    b.open,
                    b.high,
                    b.low,
                    b.close,
                    b.volume,
                    b.source,
                    fetched_at,
                    raw,
                )
                for b in bars
            ],
        )
        return cur.rowcount


def load_daily_bars(db_path: Path, symbols: list[str]) -> list[dict]:
    if not db_path.exists() or not symbols:
        return []

    placeholders = ",".join("?" for _ in symbols)
    query = f"""
        SELECT symbol, ts_utc, open, high, low, close, volume
        FROM daily_bars
        WHERE symbol IN ({placeholders})
        ORDER BY ts_utc ASC
    """
    with _connect(db_path) as conn:
        rows = conn.execute(query, [s.upper() for s in symbols]).fetchall()
    return [
        {
            "symbol": row[0],
            "timestamp": row[1],
            "open": float(row[2]),
            "high": float(row[3]),
            "low": float(row[4]),
            "close": float(row[5]),
            "volume": float(row[6]),
        }
        for row in rows
    ]
