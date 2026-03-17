from __future__ import annotations

import json
import sqlite3
from pathlib import Path


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return row is not None


def init_persistence_db(db_path: Path) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                as_of TEXT,
                event_type TEXT,
                horizon TEXT,
                floor_value REAL,
                ceiling_value REAL,
                model_version TEXT,
                payload_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                as_of TEXT,
                horizon TEXT,
                action TEXT,
                confidence REAL,
                payload_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                as_of TEXT,
                action TEXT,
                qty INTEGER,
                order_type TEXT,
                mode TEXT,
                payload_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS training_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                as_of TEXT,
                model_name TEXT,
                action TEXT,
                reason TEXT,
                data_drift REAL,
                concept_drift REAL,
                calibration_drift REAL,
                performance_decay REAL,
                payload_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS model_competition_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                as_of TEXT,
                version TEXT,
                horizon TEXT,
                model_id TEXT,
                model_family TEXT,
                is_champion INTEGER,
                mae_floor REAL,
                mae_ceiling REAL,
                mae_spread REAL,
                test_floor_coverage REAL,
                test_ceiling_coverage REAL,
                payload_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS model_training_cycles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                as_of TEXT NOT NULL,
                task TEXT NOT NULL,
                training_mode TEXT NOT NULL,
                action TEXT NOT NULL,
                champion_decision TEXT,
                model_name TEXT,
                model_version TEXT,
                retrained INTEGER NOT NULL,
                previous_champion_path TEXT,
                previous_champion_version TEXT,
                new_champion_path TEXT,
                challenger_path TEXT,
                metrics_path TEXT,
                dataset_path TEXT,
                output_dir TEXT,
                cv_enabled INTEGER NOT NULL,
                cv_folds INTEGER,
                hyperparameter_grid_json TEXT,
                tuning_summary_json TEXT,
                payload_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS prediction_reconciliations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prediction_id INTEGER NOT NULL UNIQUE,
                symbol TEXT NOT NULL,
                horizon TEXT NOT NULL,
                predicted_as_of TEXT NOT NULL,
                resolved_at TEXT NOT NULL,
                model_version TEXT,
                window_start TEXT,
                window_end TEXT,
                window_sessions INTEGER,
                predicted_floor REAL,
                predicted_ceiling REAL,
                realized_floor REAL,
                realized_ceiling REAL,
                abs_error_floor REAL,
                abs_error_ceiling REAL,
                m3_predicted_week INTEGER,
                m3_realized_week INTEGER,
                m3_week_hit INTEGER,
                payload_json TEXT NOT NULL
            )
            """
        )


def persist_payload(db_path: Path, stream: str, payload: dict) -> None:
    init_persistence_db(db_path)
    raw = json.dumps(payload, ensure_ascii=False)

    with _connect(db_path) as conn:
        if stream == "predictions":
            conn.execute(
                """
                INSERT INTO predictions(symbol, as_of, event_type, horizon, floor_value, ceiling_value, model_version, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.get("symbol"),
                    payload.get("as_of"),
                    payload.get("event_type"),
                    payload.get("horizon"),
                    payload.get("floor_value"),
                    payload.get("ceiling_value"),
                    payload.get("model_version"),
                    raw,
                ),
            )
        elif stream == "signals":
            conn.execute(
                """
                INSERT INTO signals(symbol, as_of, horizon, action, confidence, payload_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.get("symbol"),
                    payload.get("as_of"),
                    payload.get("horizon"),
                    payload.get("action"),
                    payload.get("confidence"),
                    raw,
                ),
            )
        elif stream == "orders":
            conn.execute(
                """
                INSERT INTO orders(symbol, as_of, action, qty, order_type, mode, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.get("symbol"),
                    payload.get("as_of"),
                    payload.get("action"),
                    payload.get("qty"),
                    payload.get("order_type"),
                    payload.get("mode"),
                    raw,
                ),
            )
        elif stream == "training" and str(payload.get("model_name", "")):
            conn.execute(
                """
                INSERT INTO training_reviews(as_of, model_name, action, reason, data_drift, concept_drift, calibration_drift, performance_decay, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.get("as_of"),
                    payload.get("model_name"),
                    payload.get("action"),
                    payload.get("reason"),
                    payload.get("data_drift"),
                    payload.get("concept_drift"),
                    payload.get("calibration_drift"),
                    payload.get("performance_decay"),
                    raw,
                ),
            )
        elif stream == "model_competition" and str(payload.get("model_id", "")):
            metrics = payload.get("metrics", {}) if isinstance(payload.get("metrics"), dict) else {}
            conn.execute(
                """
                INSERT INTO model_competition_results(
                    as_of, version, horizon, model_id, model_family, is_champion,
                    mae_floor, mae_ceiling, mae_spread, test_floor_coverage, test_ceiling_coverage,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.get("as_of"),
                    payload.get("version"),
                    payload.get("horizon"),
                    payload.get("model_id"),
                    payload.get("model_family"),
                    1 if bool(payload.get("is_champion")) else 0,
                    metrics.get("mae_floor"),
                    metrics.get("mae_ceiling"),
                    metrics.get("mae_spread"),
                    metrics.get("test_floor_coverage"),
                    metrics.get("test_ceiling_coverage"),
                    raw,
                ),
            )
        elif stream == "model_training_cycle" and str(payload.get("task", "")):
            conn.execute(
                """
                INSERT INTO model_training_cycles(
                    as_of, task, training_mode, action, champion_decision,
                    model_name, model_version, retrained,
                    previous_champion_path, previous_champion_version,
                    new_champion_path, challenger_path,
                    metrics_path, dataset_path, output_dir,
                    cv_enabled, cv_folds, hyperparameter_grid_json, tuning_summary_json,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.get("as_of"),
                    payload.get("task"),
                    payload.get("training_mode"),
                    payload.get("action"),
                    payload.get("champion_decision"),
                    payload.get("model_name"),
                    payload.get("model_version"),
                    1 if bool(payload.get("retrained")) else 0,
                    payload.get("previous_champion_path"),
                    payload.get("previous_champion_version"),
                    payload.get("new_champion_path"),
                    payload.get("challenger_path"),
                    payload.get("metrics_path"),
                    payload.get("dataset_path"),
                    payload.get("output_dir"),
                    1 if bool(payload.get("cv_enabled")) else 0,
                    payload.get("cv_folds"),
                    json.dumps(payload.get("hyperparameter_grid"), ensure_ascii=False),
                    json.dumps(payload.get("tuning_summary"), ensure_ascii=False),
                    raw,
                ),
            )
        elif stream == "prediction_reconciliation" and payload.get("prediction_id") is not None:
            conn.execute(
                """
                INSERT INTO prediction_reconciliations(
                    prediction_id, symbol, horizon, predicted_as_of, resolved_at, model_version,
                    window_start, window_end, window_sessions,
                    predicted_floor, predicted_ceiling,
                    realized_floor, realized_ceiling,
                    abs_error_floor, abs_error_ceiling,
                    m3_predicted_week, m3_realized_week, m3_week_hit,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(prediction_id) DO NOTHING
                """,
                (
                    payload.get("prediction_id"),
                    payload.get("symbol"),
                    payload.get("horizon"),
                    payload.get("predicted_as_of"),
                    payload.get("resolved_at"),
                    payload.get("model_version"),
                    payload.get("window_start"),
                    payload.get("window_end"),
                    payload.get("window_sessions"),
                    payload.get("predicted_floor"),
                    payload.get("predicted_ceiling"),
                    payload.get("realized_floor"),
                    payload.get("realized_ceiling"),
                    payload.get("abs_error_floor"),
                    payload.get("abs_error_ceiling"),
                    payload.get("m3_predicted_week"),
                    payload.get("m3_realized_week"),
                    1 if bool(payload.get("m3_week_hit")) else 0 if payload.get("m3_week_hit") is not None else None,
                    raw,
                ),
            )


def stream_count(db_path: Path, stream: str) -> int:
    if not db_path.exists():
        return 0
    with _connect(db_path) as conn:
        if not _table_exists(conn, stream):
            return 0
        row = conn.execute(f"SELECT COUNT(*) FROM {stream}").fetchone()
    return int(row[0]) if row else 0


def latest_predictions(db_path: Path) -> list[dict]:
    if not db_path.exists():
        return []
    query = """
      SELECT p.payload_json
      FROM predictions p
      JOIN (
        SELECT symbol, horizon, MAX(id) AS max_id
        FROM predictions
        GROUP BY symbol, horizon
      ) x ON p.id = x.max_id
      ORDER BY p.symbol, p.horizon
    """
    with _connect(db_path) as conn:
        if not _table_exists(conn, "predictions"):
            return []
        rows = conn.execute(query).fetchall()
    return [json.loads(r[0]) for r in rows]
