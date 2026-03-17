from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from floor.persistence_db import latest_predictions, stream_count
from floor.schemas import MULTI_HORIZON_PREDICTION_CONTRACT
from floor.storage import load_jsonl_rows


def _safe_ts(value: Any) -> str:
    text = str(value or "").strip()
    return text


def _dedupe_latest_predictions(rows: list[dict]) -> list[dict]:
    """Keep the latest prediction per (symbol, horizon), sorted deterministically."""
    latest: dict[tuple[str, str], tuple[str, int, dict]] = {}
    for idx, row in enumerate(rows):
        symbol = str(row.get("symbol", "")).upper()
        horizon = str(row.get("horizon", "")).lower()
        if not symbol or not horizon:
            continue
        ts = _safe_ts(row.get("as_of"))
        key = (symbol, horizon)
        prev = latest.get(key)
        if prev is None or (ts, idx) >= (prev[0], prev[1]):
            latest[key] = (ts, idx, row)
    return [item[2] for item in sorted(latest.values(), key=lambda x: (str(x[2].get("symbol", "")), str(x[2].get("horizon", ""))))]


def _dedupe_operational_count(rows: list[dict], kind: str) -> int:
    """Count unique operational records by key + timestamp.

    Predictions: symbol+horizon+as_of.
    Signals: symbol+horizon+as_of+action.
    """
    seen: set[tuple[str, ...]] = set()
    for row in rows:
        symbol = str(row.get("symbol", "")).upper()
        horizon = str(row.get("horizon", "")).lower()
        as_of = _safe_ts(row.get("as_of"))
        if not symbol:
            continue
        if kind == "signals":
            seen.add((symbol, horizon, as_of, str(row.get("action", ""))))
        else:
            seen.add((symbol, horizon, as_of))
    return len(seen)


def _collect_jsonl_stream(stream_dir: Path) -> tuple[list[dict], int]:
    rows: list[dict] = []
    empty_files = 0
    for file_path in sorted(stream_dir.glob("*.jsonl")):
        loaded = load_jsonl_rows(file_path)
        if not loaded:
            empty_files += 1
            continue
        rows.extend(loaded)
    return rows, empty_files


def _build_candidate_snapshot(data_dir: Path) -> dict[str, Any]:
    pred_files = sorted((data_dir / "predictions").glob("*.jsonl"))
    signal_files = sorted((data_dir / "signals").glob("*.jsonl"))

    warnings: list[str] = []
    db_path = data_dir / "persistence" / "app.sqlite"
    db_prediction_count = stream_count(db_path, "predictions")
    db_signal_count = stream_count(db_path, "signals")

    if db_prediction_count > 0:
        latest_preds = latest_predictions(db_path)
        source = "sqlite"
        prediction_count = db_prediction_count
        signal_count = db_signal_count
    else:
        pred_rows, empty_pred_files = _collect_jsonl_stream(data_dir / "predictions")
        signal_rows, empty_signal_files = _collect_jsonl_stream(data_dir / "signals")
        latest_preds = _dedupe_latest_predictions(pred_rows)
        source = "jsonl"
        prediction_count = _dedupe_operational_count(pred_rows, kind="predictions")
        signal_count = _dedupe_operational_count(signal_rows, kind="signals")
        if empty_pred_files:
            warnings.append(f"empty_prediction_files={empty_pred_files}")
        if empty_signal_files:
            warnings.append(f"empty_signal_files={empty_signal_files}")

    if pred_files and prediction_count == 0:
        warnings.append("inconsistent_prediction_counts:prediction_files_present_but_zero_records")
    if signal_files and signal_count == 0:
        warnings.append("inconsistent_signal_counts:signal_files_present_but_zero_records")
    if prediction_count > 0 and not latest_preds:
        warnings.append("dashboard_incomplete:prediction_count_positive_but_latest_predictions_empty")

    return {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "source": source,
        "prediction_files": len(pred_files),
        "signal_files": len(signal_files),
        "prediction_count": prediction_count,
        "signal_count": signal_count,
        "latest_predictions": latest_preds,
        "latest_predictions_source": source,
        "prediction_contract": MULTI_HORIZON_PREDICTION_CONTRACT,
        "validation": {
            "ok": len(warnings) == 0,
            "warnings": warnings,
        },
    }


def _read_existing_snapshot(output_path: Path) -> dict[str, Any] | None:
    if not output_path.exists():
        return None
    try:
        return json.loads(output_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _prefer_existing(existing: dict[str, Any], candidate: dict[str, Any]) -> bool:
    existing_pred = int(existing.get("prediction_count", 0) or 0)
    candidate_pred = int(candidate.get("prediction_count", 0) or 0)
    if candidate_pred >= existing_pred:
        return False

    existing_signal = int(existing.get("signal_count", 0) or 0)
    candidate_signal = int(candidate.get("signal_count", 0) or 0)
    existing_source = str(existing.get("source", ""))
    candidate_source = str(candidate.get("source", ""))

    return (
        existing_source == "sqlite"
        and candidate_source == "jsonl"
        and (existing_pred > candidate_pred or existing_signal > candidate_signal)
    )


def build_dashboard_snapshot(data_dir: Path, output_path: Path) -> None:
    candidate = _build_candidate_snapshot(data_dir)
    existing = _read_existing_snapshot(output_path)

    if existing and _prefer_existing(existing, candidate):
        warnings = list(candidate.get("validation", {}).get("warnings", []))
        warnings.append("kept_previous_snapshot:existing_more_complete_than_candidate")
        existing.setdefault("validation", {})
        existing["validation"]["ok"] = False
        existing["validation"]["warnings"] = sorted(set([*existing["validation"].get("warnings", []), *warnings]))
        payload = existing
    else:
        payload = candidate

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
