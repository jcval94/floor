from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


REQUIRED_TABLES = {
    "predictions",
    "signals",
    "orders",
    "training_reviews",
    "model_competition_results",
    "model_training_cycles",
}


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {str(r[0]) for r in rows}


def run(db_path: Path) -> int:
    if not db_path.exists():
        print(f"ERROR: db_not_found path={db_path}")
        return 2

    with _connect(db_path) as conn:
        integrity = conn.execute("PRAGMA integrity_check;").fetchone()[0]
        print(f"integrity_check={integrity}")
        if integrity != "ok":
            return 1

        tables = _table_names(conn)
        missing = sorted(REQUIRED_TABLES.difference(tables))
        print(f"tables_present={len(tables)} required_missing={missing}")
        if missing:
            return 1

        for table in sorted(REQUIRED_TABLES):
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"rows[{table}]={count}")

        dups = conn.execute(
            """
            SELECT COUNT(*) FROM (
              SELECT symbol, as_of, event_type, horizon, COUNT(*) c
              FROM predictions
              GROUP BY symbol, as_of, event_type, horizon
              HAVING c > 1
            )
            """
        ).fetchone()[0]
        print(f"prediction_duplicate_keys={dups}")

        auto = conn.execute("PRAGMA auto_vacuum;").fetchone()[0]
        wal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        print(f"journal_mode={wal_mode} auto_vacuum={auto}")

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="DB health check for persistence SQLite")
    parser.add_argument("--db", default="data/persistence/app.sqlite", help="Path to SQLite DB")
    args = parser.parse_args()
    raise SystemExit(run(Path(args.db)))


if __name__ == "__main__":
    main()
