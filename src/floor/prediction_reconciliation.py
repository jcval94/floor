from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import sqlite3
from datetime import timezone
from pathlib import Path
from typing import Any

from floor.persistence_db import persist_payload
from floor.storage import load_jsonl_rows

logger = logging.getLogger(__name__)

_REQUIRED_SESSIONS = {"d1": 1, "w1": 5, "q1": 10, "m3": 65}


def _parse_iso_date(value: str) -> dt.date:
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    return dt.datetime.fromisoformat(raw).date()


def _load_symbol_bars(market_db_path: Path) -> dict[str, list[dict[str, Any]]]:
    if not market_db_path.exists():
        return {}
    with sqlite3.connect(market_db_path) as conn:
        rows = conn.execute(
            """
            SELECT symbol, ts_utc, low, high
            FROM daily_bars
            ORDER BY symbol ASC, ts_utc ASC
            """
        ).fetchall()

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


def prediction_key(payload: dict[str, Any]) -> str:
    """Stable semantic identity independent of ephemeral SQLite row ids."""

    batch_id = str(payload.get("batch_id") or "").strip()
    symbol = str(payload.get("symbol") or "").strip().upper()
    horizon = str(payload.get("horizon") or "").strip().lower()
    model_version = str(payload.get("model_version") or "").strip()
    if batch_id:
        raw = f"batch={batch_id}|symbol={symbol}|horizon={horizon}|model={model_version}"
    else:
        raw = (
            f"as_of={payload.get('as_of')}|event={payload.get('event_type')}|"
            f"symbol={symbol}|horizon={horizon}|model={model_version}"
        )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _stable_prediction_id(key: str) -> int:
    # Fits comfortably in signed SQLite INTEGER while being deterministic.
    return int(key[:15], 16)


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
    """Reconcile durable prediction JSONL against subsequently realized bars.

    SQLite row ids are deliberately not the source of truth. This lets a fresh
    GitHub Actions runner reconcile q1/m3 predictions created many sessions ago.
    Reconciliation evidence is itself written to durable JSONL and mirrored to
    SQLite for current-run querying.
    """

    db_path = data_dir / "persistence" / "app.sqlite"
    market_db_path = data_dir / "market" / "market_data.sqlite"
    if not market_db_path.exists():
        logger.info("[reconcile] market db missing path=%s", market_db_path)
        return {"pending": 0, "reconciled": 0, "skipped": 0}

    symbol_bars = _load_symbol_bars(market_db_path)
    already_reconciled = _reconciliation_ledger_keys(data_dir)
    ledger = _prediction_ledger(data_dir)
    pending = [
        row
        for row in ledger
        if str(row.get("prediction_key") or "") not in already_reconciled
    ]
    reconciled = 0
    skipped = 0
    now = dt.datetime.now(tz=timezone.utc).isoformat()

    for pred in pending:
        horizon = str(pred["horizon"])
        required_sessions = _REQUIRED_SESSIONS[horizon]
        bars = symbol_bars.get(str(pred["symbol"])) or []
        if not bars:
            skipped += 1
            continue

        try:
            as_of_date = _parse_iso_date(str(pred["as_of"]))
        except (ValueError, TypeError):
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
            "prediction_id": _stable_prediction_id(key),
            "prediction_key": key,
            "batch_id": pred.get("batch_id"),
            "symbol": pred["symbol"],
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
                None
                if predicted_floor is None
                else abs(predicted_floor - realized_floor)
            ),
            "abs_error_ceiling": (
                None
                if predicted_ceiling is None
                else abs(predicted_ceiling - realized_ceiling)
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
        persist_payload(
            db_path=db_path,
            stream="prediction_reconciliation",
            payload=payload,
        )
        if _append_reconciliation_once(data_dir, payload, already_reconciled):
            reconciled += 1

    logger.info(
        "[reconcile] ledger=%s already=%s pending=%s reconciled=%s skipped=%s",
        len(ledger),
        len(already_reconciled),
        len(pending),
        reconciled,
        skipped,
    )
    return {"pending": len(pending), "reconciled": reconciled, "skipped": skipped}
