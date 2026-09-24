from __future__ import annotations

import datetime as dt
import json
import logging
import sqlite3
from datetime import timezone
from pathlib import Path
from typing import Any

from floor.persistence_db import persist_payload
from floor.prediction_identity import prediction_key, stable_prediction_id
from floor.storage import load_jsonl_rows

logger = logging.getLogger(__name__)

_REQUIRED_SESSIONS = {"d1": 1, "w1": 5, "q1": 10, "m3": 65}


def _parse_iso_date(value: str) -> dt.date:
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    return dt.datetime.fromisoformat(raw).date()


def _load_symbol_bars(
    market_db_path: Path,
    *,
    symbols: set[str],
    start_date: dt.date,
) -> dict[str, list[dict[str, Any]]]:
    if not market_db_path.exists() or not symbols:
        return {}

    ordered_symbols = sorted(symbols)
    placeholders = ",".join("?" for _ in ordered_symbols)
    query = f"""
        SELECT symbol, ts_utc, low, high
        FROM daily_bars
        WHERE symbol IN ({placeholders})
          AND ts_utc >= ?
        ORDER BY symbol ASC, ts_utc ASC
    """
    params: list[object] = [*ordered_symbols, start_date.isoformat()]
    with sqlite3.connect(market_db_path) as conn:
        rows = conn.execute(query, params).fetchall()

    out: dict[str, list[dict[str, Any]]] = {}
    for symbol, ts_utc, low, high in rows:
        out.setdefault(str(symbol).upper(), []).append(
            {
                "date": _parse_iso_date(str(ts_utc)),
                "ts_utc": str(ts_utc),
                "low": float(low),
                "high": float(high),
            }
        )
    return out


def _prediction_ledger(data_dir: Path) -> list[dict[str, Any]]:
    directory = data_dir / "predictions"
    if not directory.exists():
        return []
    by_key: dict[str, dict[str, Any]] = {}
    for path in sorted(directory.glob("*.jsonl")):
        for raw in load_jsonl_rows(path):
            if not isinstance(raw, dict):
                continue
            payload: dict[str, Any] = {str(key): value for key, value in raw.items()}
            symbol = str(payload.get("symbol") or "").strip().upper()
            horizon = str(payload.get("horizon") or "").strip().lower()
            as_of = str(payload.get("as_of") or "").strip()
            if not symbol or horizon not in _REQUIRED_SESSIONS or not as_of:
                continue
            payload["symbol"] = symbol
            payload["horizon"] = horizon
            key = prediction_key(payload)
            payload["prediction_key"] = key
            by_key.setdefault(key, payload)
    return list(by_key.values())


def _pending_predictions_from_sqlite(
    db_path: Path,
) -> tuple[list[dict[str, Any]], int, int] | None:
    """Use the reconstructable cache as an index, never as the durable ledger."""

    if not db_path.exists():
        return None

    with sqlite3.connect(db_path) as conn:
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "predictions" not in tables or "prediction_reconciliations" not in tables:
            return None

        columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(predictions)").fetchall()
        }
        if "prediction_key" not in columns:
            return None

        missing_key_row = conn.execute(
            """
            SELECT 1
            FROM predictions
            WHERE prediction_key IS NULL OR prediction_key = ''
            LIMIT 1
            """
        ).fetchone()
        if missing_key_row is not None:
            return None

        ledger_count_row = conn.execute("SELECT COUNT(*) FROM predictions").fetchone()
        reconciled_count_row = conn.execute(
            """
            SELECT COUNT(*)
            FROM prediction_reconciliations
            WHERE prediction_key IS NOT NULL AND prediction_key <> ''
            """
        ).fetchone()
        rows = conn.execute(
            """
            SELECT p.prediction_key, p.payload_json
            FROM predictions AS p
            LEFT JOIN prediction_reconciliations AS r
              ON r.prediction_key = p.prediction_key
            WHERE p.prediction_key IS NOT NULL
              AND p.prediction_key <> ''
              AND r.prediction_key IS NULL
            ORDER BY p.id ASC
            """
        ).fetchall()

    pending: list[dict[str, Any]] = []
    for key, raw_payload in rows:
        try:
            raw = json.loads(str(raw_payload))
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict):
            continue
        payload: dict[str, Any] = {str(name): value for name, value in raw.items()}
        symbol = str(payload.get("symbol") or "").strip().upper()
        horizon = str(payload.get("horizon") or "").strip().lower()
        as_of = str(payload.get("as_of") or "").strip()
        if not symbol or horizon not in _REQUIRED_SESSIONS or not as_of:
            continue
        payload["symbol"] = symbol
        payload["horizon"] = horizon
        payload["prediction_key"] = str(key)
        pending.append(payload)

    ledger_count = int(ledger_count_row[0]) if ledger_count_row else 0
    reconciled_count = int(reconciled_count_row[0]) if reconciled_count_row else 0
    return pending, ledger_count, reconciled_count


def _reconciliation_directory(data_dir: Path) -> Path:
    # Durable reconciliation evidence lives beside the prediction ledger so it
    # survives ephemeral SQLite runners and is carried by runtime-state storage.
    return data_dir / "predictions" / "reconciliations"


def _reconciliation_ledger_keys(data_dir: Path) -> set[str]:
    directory = _reconciliation_directory(data_dir)
    if not directory.exists():
        return set()
    keys: set[str] = set()
    for path in sorted(directory.glob("*.jsonl")):
        for row in load_jsonl_rows(path):
            key = str(row.get("prediction_key") or "").strip()
            if key:
                keys.add(key)
    return keys


def _reconciliation_symbol_keys(data_dir: Path, symbol: str) -> set[str]:
    path = _reconciliation_directory(data_dir) / f"{symbol.upper()}.jsonl"
    if not path.exists():
        return set()
    keys: set[str] = set()
    for row in load_jsonl_rows(path):
        key = str(row.get("prediction_key") or "").strip()
        if key:
            keys.add(key)
    return keys


def _append_reconciliation_once(
    data_dir: Path,
    payload: dict[str, Any],
    known_keys: set[str] | None = None,
) -> bool:
    key = str(payload.get("prediction_key") or "").strip()
    if not key:
        raise ValueError("reconciliation payload missing prediction_key")

    keys = known_keys if known_keys is not None else _reconciliation_ledger_keys(data_dir)
    if key in keys:
        return False

    path = _reconciliation_directory(data_dir) / f"{payload['symbol']}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        handle.flush()
    keys.add(key)
    return True


def _week_index_for_floor(window: list[dict[str, Any]]) -> int | None:
    if not window:
        return None
    floor_idx = min(range(len(window)), key=lambda idx: float(window[idx]["low"]))
    return floor_idx // 5 + 1


def _optional_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    if not isinstance(value, (int, float, str, bytes, bytearray)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: object) -> int | None:
    number = _optional_float(value)
    if number is None or number != float(int(number)):
        return None
    return int(number)


def reconcile_predictions(data_dir: Path) -> dict[str, int]:
    """Reconcile only pending predictions against subsequently realized daily bars.

    Durable JSONL remains authoritative. SQLite is a reconstructable index that
    avoids reparsing the full prediction/reconciliation history on every EOD.
    """

    db_path = data_dir / "persistence" / "app.sqlite"
    market_db_path = data_dir / "market" / "market_data.sqlite"
    if not market_db_path.exists():
        logger.info("[reconcile] market db missing path=%s", market_db_path)
        return {"pending": 0, "reconciled": 0, "skipped": 0}

    indexed = _pending_predictions_from_sqlite(db_path)
    if indexed is None:
        already_reconciled = _reconciliation_ledger_keys(data_dir)
        ledger = _prediction_ledger(data_dir)
        pending = [
            row
            for row in ledger
            if str(row.get("prediction_key") or "") not in already_reconciled
        ]
        ledger_count = len(ledger)
        reconciled_count = len(already_reconciled)
        source = "jsonl_fallback"
    else:
        pending, ledger_count, reconciled_count = indexed
        source = "sqlite_index"

    if not pending:
        logger.info(
            "[reconcile] source=%s ledger=%s already=%s pending=0 reconciled=0 skipped=0",
            source,
            ledger_count,
            reconciled_count,
        )
        return {"pending": 0, "reconciled": 0, "skipped": 0}

    parsed_pending: list[tuple[dict[str, Any], dt.date]] = []
    skipped = 0
    for pred in pending:
        try:
            as_of_date = _parse_iso_date(str(pred["as_of"]))
        except (ValueError, TypeError):
            skipped += 1
            continue
        parsed_pending.append((pred, as_of_date))

    if not parsed_pending:
        return {"pending": len(pending), "reconciled": 0, "skipped": skipped}

    symbols = {str(pred["symbol"]).upper() for pred, _ in parsed_pending}
    earliest_future_date = min(
        as_of_date + dt.timedelta(days=1) for _, as_of_date in parsed_pending
    )
    symbol_bars = _load_symbol_bars(
        market_db_path,
        symbols=symbols,
        start_date=earliest_future_date,
    )

    reconciled = 0
    now = dt.datetime.now(tz=timezone.utc).isoformat()
    durable_keys_by_symbol: dict[str, set[str]] = {}

    for pred, as_of_date in parsed_pending:
        horizon = str(pred["horizon"])
        required_sessions = _REQUIRED_SESSIONS[horizon]
        symbol = str(pred["symbol"]).upper()
        bars = symbol_bars.get(symbol) or []
        if not bars:
            skipped += 1
            continue

        future = [bar for bar in bars if bar["date"] > as_of_date]
        if len(future) < required_sessions:
            skipped += 1
            continue

        window = future[:required_sessions]
        floor_bar = min(window, key=lambda item: float(item["low"]))
        ceiling_bar = max(window, key=lambda item: float(item["high"]))
        realized_floor = float(floor_bar["low"])
        realized_ceiling = float(ceiling_bar["high"])
        predicted_floor = _optional_float(pred.get("floor_value"))
        predicted_ceiling = _optional_float(pred.get("ceiling_value"))

        m3_pred_week = (
            _optional_int(pred.get("floor_week_m3")) if horizon == "m3" else None
        )
        m3_real_week = _week_index_for_floor(window) if horizon == "m3" else None
        key = str(pred["prediction_key"])
        payload: dict[str, Any] = {
            "prediction_id": stable_prediction_id(key),
            "prediction_key": key,
            "batch_id": pred.get("batch_id"),
            "symbol": symbol,
            "horizon": horizon,
            "predicted_as_of": pred["as_of"],
            "resolved_at": now,
            "model_version": pred.get("model_version") or None,
            "window_start": window[0]["ts_utc"],
            "window_end": window[-1]["ts_utc"],
            "window_sessions": len(window),
            "predicted_floor": predicted_floor,
            "predicted_ceiling": predicted_ceiling,
            "realized_floor": realized_floor,
            "realized_ceiling": realized_ceiling,
            "abs_error_floor": (
                None if predicted_floor is None else abs(predicted_floor - realized_floor)
            ),
            "abs_error_ceiling": (
                None if predicted_ceiling is None else abs(predicted_ceiling - realized_ceiling)
            ),
            "m3_predicted_week": m3_pred_week,
            "m3_realized_week": m3_real_week,
            "m3_week_hit": (
                m3_pred_week == m3_real_week
                if m3_pred_week is not None and m3_real_week is not None
                else None
            ),
            "realized_floor_at": floor_bar["ts_utc"],
            "realized_ceiling_at": ceiling_bar["ts_utc"],
        }

        if symbol not in durable_keys_by_symbol:
            durable_keys_by_symbol[symbol] = _reconciliation_symbol_keys(data_dir, symbol)
        appended = _append_reconciliation_once(
            data_dir,
            payload,
            durable_keys_by_symbol[symbol],
        )
        persist_payload(
            db_path=db_path,
            stream="prediction_reconciliation",
            payload=payload,
        )
        if appended:
            reconciled += 1

    logger.info(
        "[reconcile] source=%s ledger=%s already=%s pending=%s reconciled=%s skipped=%s "
        "market_symbols=%s market_start=%s",
        source,
        ledger_count,
        reconciled_count,
        len(pending),
        reconciled,
        skipped,
        len(symbols),
        earliest_future_date.isoformat(),
    )
    return {"pending": len(pending), "reconciled": reconciled, "skipped": skipped}
