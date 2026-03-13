from __future__ import annotations

import json
from pathlib import Path

from floor.persistence_db import persist_payload
from floor.schemas import record_to_dict


def _find_data_root(path: Path) -> Path | None:
    parts = list(path.parts)
    if "data" not in parts:
        return None
    idx = parts.index("data")
    root_parts = parts[: idx + 1]
    return Path(*root_parts)


def append_jsonl(path: Path, record: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = record_to_dict(record) if not isinstance(record, dict) else record
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")

    data_root = _find_data_root(path)
    if data_root is None:
        return
    stream = path.parent.name
    db_path = data_root / "persistence" / "app.sqlite"
    persist_payload(db_path=db_path, stream=stream, payload=payload)
