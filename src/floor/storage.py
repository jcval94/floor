from __future__ import annotations

import json
from pathlib import Path

from floor.schemas import record_to_dict


def append_jsonl(path: Path, record: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = record_to_dict(record) if not isinstance(record, dict) else record
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")
