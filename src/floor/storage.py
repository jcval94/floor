from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from floor.persistence_db import persist_payload
from floor.schemas import record_to_dict


def _find_data_root(path: Path) -> Path | None:
    parts = list(path.parts)
    if "data" not in parts:
        return None
    idx = parts.index("data")
    root_parts = parts[: idx + 1]
    return Path(*root_parts)


def load_jsonl_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _idempotency_key(payload: dict[str, Any]) -> tuple[str, str, str] | None:
    batch_id = str(payload.get("batch_id") or "").strip()
    symbol = str(payload.get("symbol") or "").strip().upper()
    horizon = str(payload.get("horizon") or "").strip().lower()
    if not batch_id or not symbol or not horizon:
        return None
    return batch_id, symbol, horizon


def _jsonl_contains_key(path: Path, key: tuple[str, str, str]) -> bool:
    if not path.exists():
        return False
    # Stream rather than materializing an ever-growing ledger. Corrupt JSON is
    # intentionally fail-closed: silently skipping a damaged durable row would
    # make a duplicate look safe to append.
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if isinstance(row, dict) and _idempotency_key(row) == key:
                return True
    return False


def append_jsonl(path: Path, record: object, *, batch_id: str = "") -> bool:
    """Append one durable payload and mirror it to reconstructable SQLite.

    Predictions/signals carrying a batch id are idempotent by
    ``(batch_id, symbol, horizon)``. JSONL is the source of truth and is written
    before the SQLite cache. If SQLite persistence fails after the ledger write,
    a retry/hydration can reconstruct the cache without losing the durable row.
    Returning ``False`` means the logical row already existed in JSONL.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    raw_payload = record_to_dict(record) if not isinstance(record, dict) else dict(record)
    payload: dict[str, Any] = {str(key): value for key, value in raw_payload.items()}
    if batch_id:
        payload["batch_id"] = batch_id

    key = _idempotency_key(payload)
    duplicate = key is not None and _jsonl_contains_key(path, key)

    data_root = _find_data_root(path)
    if duplicate:
        # A previous attempt may have written the durable ledger and crashed
        # before SQLite. Repair/confirm the cache even on a duplicate retry.
        if data_root is not None:
            stream = path.parent.name
            db_path = data_root / "persistence" / "app.sqlite"
            persist_payload(db_path=db_path, stream=stream, payload=payload)
        return False

    serialized = json.dumps(payload, ensure_ascii=False) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(serialized)
        handle.flush()

    if data_root is not None:
        stream = path.parent.name
        db_path = data_root / "persistence" / "app.sqlite"
        persist_payload(db_path=db_path, stream=stream, payload=payload)
    return True
