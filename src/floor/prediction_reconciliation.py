from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from floor.persistence_db import persist_payload

logger = logging.getLogger(__name__)


_REQUIRED_SESSIONS = {"d1": 1, "w1": 5, "q1": 10, "m3": 65}


def _parse_iso_date(value: str) -> date:
    v = value.strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    return datetime.fromisoformat(v).date()


def _load_symbol_bars(market_db_path: Path) -> dict[str, list[dict]]:
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

    out: dict[str, list[dict]] = {}
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


def _pending_predictions(db_path: Path) -> list[dict]:
    if not db_path.exists():
        return []
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT p.id, p.symbol, p.as_of, p.horizon, p.floor_value, p.ceiling_value, p.model_version, p.payload_json
            FROM predictions p
            LEFT JOIN prediction_reconciliations r ON r.prediction_id = p.id
            WHERE r.prediction_id IS NULL
            ORDER BY p.id ASC
            """
        ).fetchall()

    pending: list[dict] = []
    for row in rows:
        payload = json.loads(row[7]) if row[7] else {}
        pending.append(
            {
                "prediction_id": int(row[0]),
                "symbol": str(row[1]).upper(),
                "as_of": str(row[2]),
                "horizon": str(row[3]).lower(),
                "floor_value": None if row[4] is None else float(row[4]),
                "ceiling_value": None if row[5] is None else float(row[5]),
                "model_version": str(row[6] or payload.get("model_version") or ""),
                "payload": payload,
            }
        )
    return pending


def _week_index_for_floor(window: list[dict]) -> int | None:
    if not window:
        return None
    floor_idx = min(range(len(window)), key=lambda i: float(window[i]["low"]))
    return floor_idx // 5 + 1


def reconcile_predictions(data_dir: Path) -> dict[str, int]:
    db_path = data_dir / "persistence" / "app.sqlite"
    market_db_path = data_dir / "market" / "market_data.sqlite"
    if not db_path.exists():
        logger.info("[reconcile] sqlite db missing path=%s", db_path)
        return {"pending": 0, "reconciled": 0, "skipped": 0}
    if not market_db_path.exists():
        logger.info("[reconcile] market db missing path=%s", market_db_path)
        return {"pending": 0, "reconciled": 0, "skipped": 0}

    symbol_bars = _load_symbol_bars(market_db_path)
    pending = _pending_predictions(db_path)
    reconciled = 0
    skipped = 0

    now = datetime.now(tz=timezone.utc).isoformat()
    for pred in pending:
        horizon = pred["horizon"]
        required_sessions = _REQUIRED_SESSIONS.get(horizon)
        if required_sessions is None:
            skipped += 1
            continue

        bars = symbol_bars.get(str(pred["symbol"])) or []
        if not bars:
            skipped += 1
            continue

        as_of_date = _parse_iso_date(str(pred["as_of"]))
        future = [b for b in bars if b["date"] > as_of_date]
        if len(future) < required_sessions:
            skipped += 1
            continue

        window = future[:required_sessions]
        floor_bar = min(window, key=lambda x: float(x["low"]))
        ceiling_bar = max(window, key=lambda x: float(x["high"]))
        realized_floor = float(floor_bar["low"])
        realized_ceiling = float(ceiling_bar["high"])
        predicted_floor = pred.get("floor_value")
        predicted_ceiling = pred.get("ceiling_value")

        m3_pred_week = None
        payload_obj = pred.get("payload")
        pld: dict[str, object] = payload_obj if isinstance(payload_obj, dict) else {}
        floor_week_m3 = pld.get("floor_week_m3")
        if isinstance(floor_week_m3, int):
            m3_pred_week = floor_week_m3
        m3_real_week = _week_index_for_floor(window) if horizon == "m3" else None

        payload = {
            "prediction_id": pred["prediction_id"],
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
            "abs_error_floor": None if predicted_floor is None else abs(float(predicted_floor) - realized_floor),
            "abs_error_ceiling": None if predicted_ceiling is None else abs(float(predicted_ceiling) - realized_ceiling),
            "m3_predicted_week": m3_pred_week,
            "m3_realized_week": m3_real_week,
            "m3_week_hit": (m3_pred_week == m3_real_week) if (m3_pred_week is not None and m3_real_week is not None) else None,
            "realized_floor_at": floor_bar["ts_utc"],
            "realized_ceiling_at": ceiling_bar["ts_utc"],
        }
        persist_payload(db_path=db_path, stream="prediction_reconciliation", payload=payload)
        reconciled += 1

    logger.info("[reconcile] done pending=%s reconciled=%s skipped=%s", len(pending), reconciled, skipped)
    return {"pending": len(pending), "reconciled": reconciled, "skipped": skipped}
