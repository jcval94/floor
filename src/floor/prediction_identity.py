from __future__ import annotations

import hashlib
from typing import Any


def prediction_key(payload: dict[str, Any]) -> str:
    """Stable semantic identity independent of ephemeral SQLite row ids."""

    batch_id = str(payload.get("batch_id") or "").strip()
    symbol = str(payload.get("symbol") or "").strip().upper()
    horizon = str(payload.get("horizon") or "").strip().lower()
    model_version = str(payload.get("model_version") or "").strip()
    # Hydration assigns synthetic legacy batch ids only for SQLite idempotency.
    # They must not change the semantic identity of the original durable row.
    if batch_id and not batch_id.startswith("legacy:"):
        raw = f"batch={batch_id}|symbol={symbol}|horizon={horizon}|model={model_version}"
    else:
        raw = (
            f"as_of={payload.get('as_of')}|event={payload.get('event_type')}|"
            f"symbol={symbol}|horizon={horizon}|model={model_version}"
        )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def stable_prediction_id(key: str) -> int:
    """Deterministic SQLite-safe id derived from the semantic prediction key."""

    return int(key[:15], 16)
