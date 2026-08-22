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


def validate_latest_prediction_batch(
    data_dir: Path,
    expected_symbols: Iterable[str],
    *,
    event_type: str,
    expected_horizons: Iterable[str] = DEFAULT_HORIZONS,
) -> dict[str, object]:
    symbols = sorted({str(symbol).strip().upper() for symbol in expected_symbols if str(symbol).strip()})
    horizons = sorted({str(horizon).strip().lower() for horizon in expected_horizons if str(horizon).strip()})
    event = str(event_type).strip().upper()
    if not symbols:
        raise RuntimeError("Prediction batch validation refused: no expected symbols")
    if not horizons:
        raise RuntimeError("Prediction batch validation refused: no expected horizons")
    if not event:
        raise RuntimeError("Prediction batch validation refused: event_type is empty")

    rows = _load_prediction_rows(data_dir / "predictions")
    candidates: list[tuple[datetime, dict[str, object]]] = []
    for row in rows:
        symbol = str(row.get("symbol") or "").strip().upper()
        row_event = str(row.get("event_type") or "").strip().upper()
        horizon = str(row.get("horizon") or "").strip().lower()
        as_of = _parse_as_of(row.get("as_of"))
        if symbol in symbols and row_event == event and horizon in horizons and as_of is not None:
            candidates.append((as_of, row))

    if not candidates:
        raise RuntimeError(
            f"Prediction batch validation refused: no valid rows for event={event} symbols={len(symbols)}"
        )

    latest_as_of = max(timestamp for timestamp, _ in candidates)
    latest_rows = [row for timestamp, row in candidates if timestamp == latest_as_of]
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

    if missing or duplicates:
        details: list[str] = []
        if missing:
            details.append("missing=" + ",".join(f"{symbol}:{horizon}" for symbol, horizon in missing[:20]))
        if duplicates:
            details.append("duplicates=" + ",".join(f"{symbol}:{horizon}" for symbol, horizon in duplicates[:20]))
        raise RuntimeError(
            "Prediction batch validation refused incomplete latest batch: " + "; ".join(details)
        )

    return {
        "status": "OK",
        "event_type": event,
        "latest_as_of": latest_as_of.isoformat(),
        "symbols": len(symbols),
        "horizons": horizons,
        "expected_pairs": len(expected_pairs),
        "observed_pairs": len(observed_pairs),
    }
