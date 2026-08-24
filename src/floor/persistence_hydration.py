from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from floor.persistence_db import init_persistence_db, persist_payload
from floor.storage import load_jsonl_rows


def _legacy_batch_id(stream: str, payload: dict[str, Any]) -> str:
    as_of = str(payload.get("as_of") or "").strip()
    if not as_of:
        return ""
    if stream == "predictions":
        event = str(payload.get("event_type") or "UNKNOWN").strip()
        return f"legacy:{as_of}:{event}"
    return f"legacy:{as_of}"


def _replay_stream(data_dir: Path, stream: str) -> tuple[int, int]:
    stream_dir = data_dir / stream
    db_path = data_dir / "persistence" / "app.sqlite"
    seen = 0
    inserted = 0
    if not stream_dir.exists():
        return seen, inserted

    for path in sorted(stream_dir.glob("*.jsonl")):
        for raw in load_jsonl_rows(path):
            if not isinstance(raw, dict):
                continue
            payload: dict[str, Any] = {str(key): value for key, value in raw.items()}
            if not str(payload.get("batch_id") or "").strip():
                payload["batch_id"] = _legacy_batch_id(stream, payload)
            seen += 1
            if persist_payload(db_path=db_path, stream=stream, payload=payload):
                inserted += 1
    return seen, inserted


def hydrate_persistence_from_jsonl(data_dir: Path) -> dict[str, int]:
    """Replay durable prediction/signal ledgers into the ephemeral SQLite cache."""

    db_path = data_dir / "persistence" / "app.sqlite"
    init_persistence_db(db_path)
    prediction_seen, prediction_inserted = _replay_stream(data_dir, "predictions")
    signal_seen, signal_inserted = _replay_stream(data_dir, "signals")
    return {
        "prediction_rows_seen": prediction_seen,
        "prediction_rows_inserted": prediction_inserted,
        "signal_rows_seen": signal_seen,
        "signal_rows_inserted": signal_inserted,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Hydrate reconstructable SQLite cache from durable JSONL ledgers"
    )
    parser.add_argument("--data-dir", default="data")
    args = parser.parse_args()
    result = hydrate_persistence_from_jsonl(Path(args.data_dir))
    print(result)


if __name__ == "__main__":
    main()
