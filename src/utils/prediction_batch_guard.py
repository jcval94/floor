from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_HORIZONS = ("d1", "w1", "q1", "m3")


def _parse_as_of(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _load_prediction_rows(predictions_dir: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    if not predictions_dir.exists():
        return rows
    for path in sorted(predictions_dir.glob("*.jsonl")):
        for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"Prediction batch validation refused: invalid JSON in {path}:{line_number}"
                ) from exc
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


def inspect_latest_prediction_batch(
    data_dir: Path,
    expected_symbols: Iterable[str],
    *,
    event_type: str | None = None,
    expected_horizons: Iterable[str] = DEFAULT_HORIZONS,
) -> dict[str, object]:
    """Inspect the newest atomic prediction batch without hiding partial output.

    If ``event_type`` is omitted, the globally newest as_of is inspected and its
    event type is inferred. This mode is used by monitoring so a complete old
    event can never mask a newer partial one.
    """
    symbols = sorted(
        {str(symbol).strip().upper() for symbol in expected_symbols if str(symbol).strip()}
    )
    horizons = sorted(
        {str(horizon).strip().lower() for horizon in expected_horizons if str(horizon).strip()}
    )
    requested_event = str(event_type).strip().upper() if event_type is not None else None
    if not symbols:
        raise RuntimeError("Prediction batch validation refused: no expected symbols")
    if not horizons:
        raise RuntimeError("Prediction batch validation refused: no expected horizons")

    rows = _load_prediction_rows(data_dir / "predictions")
    candidates: list[tuple[datetime, dict[str, object]]] = []
    for row in rows:
        symbol = str(row.get("symbol") or "").strip().upper()
        row_event = str(row.get("event_type") or "").strip().upper()
        horizon = str(row.get("horizon") or "").strip().lower()
        as_of = _parse_as_of(row.get("as_of"))
        if symbol not in symbols or horizon not in horizons or as_of is None:
            continue
        if requested_event is not None and row_event != requested_event:
            continue
        candidates.append((as_of, row))

    if not candidates:
        event_text = requested_event or "latest"
        raise RuntimeError(
            f"Prediction batch validation refused: no valid rows for event={event_text} symbols={len(symbols)}"
        )

    latest_as_of = max(timestamp for timestamp, _ in candidates)
    latest_rows = [row for timestamp, row in candidates if timestamp == latest_as_of]
    latest_events = sorted(
        {str(row.get("event_type") or "").strip().upper() for row in latest_rows}
    )
    observed_pairs = [
        (
            str(row.get("symbol") or "").strip().upper(),
            str(row.get("horizon") or "").strip().lower(),
        )
        for row in latest_rows
    ]
    counts = Counter(observed_pairs)
    expected_pairs = {(symbol, horizon) for symbol in symbols for horizon in horizons}
    observed = set(observed_pairs)
    missing = sorted(expected_pairs - observed)
    duplicates = sorted(pair for pair, count in counts.items() if count > 1)
    event_mismatch = requested_event is None and len(latest_events) != 1
    complete = not missing and not duplicates and not event_mismatch

    return {
        "status": "OK" if complete else "CRITICAL",
        "complete": complete,
        "event_type": requested_event or (latest_events[0] if len(latest_events) == 1 else None),
        "events_at_latest_as_of": latest_events,
        "latest_as_of": latest_as_of.isoformat(),
        "symbols": len(symbols),
        "horizons": horizons,
        "expected_pairs": len(expected_pairs),
        "observed_pairs": len(observed_pairs),
        "missing": [f"{symbol}:{horizon}" for symbol, horizon in missing],
        "duplicates": [f"{symbol}:{horizon}" for symbol, horizon in duplicates],
        "event_mismatch": event_mismatch,
    }


def validate_latest_prediction_batch(
    data_dir: Path,
    expected_symbols: Iterable[str],
    *,
    event_type: str,
    expected_horizons: Iterable[str] = DEFAULT_HORIZONS,
) -> dict[str, object]:
    result = inspect_latest_prediction_batch(
        data_dir,
        expected_symbols,
        event_type=event_type,
        expected_horizons=expected_horizons,
    )
    if not result["complete"]:
        details: list[str] = []
        missing = result.get("missing")
        duplicates = result.get("duplicates")
        if isinstance(missing, list) and missing:
            details.append("missing=" + ",".join(str(value) for value in missing[:20]))
        if isinstance(duplicates, list) and duplicates:
            details.append("duplicates=" + ",".join(str(value) for value in duplicates[:20]))
        if result.get("event_mismatch"):
            details.append("event_mismatch=true")
        raise RuntimeError(
            "Prediction batch validation refused incomplete latest batch: " + "; ".join(details)
        )
    return result
