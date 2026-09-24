from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from floor.persistence_db import PersistenceWriter
from floor.prediction_identity import stable_prediction_id
from floor.storage import load_jsonl_rows


def _legacy_batch_id(stream: str, payload: dict[str, Any]) -> str:
    as_of = str(payload.get("as_of") or "").strip()
    if not as_of:
        return ""
    if stream == "predictions":
        event = str(payload.get("event_type") or "UNKNOWN").strip()
        return f"legacy:{as_of}:{event}"
    return f"legacy:{as_of}"


def _replay_stream(data_dir: Path, stream: str, writer: PersistenceWriter) -> tuple[int, int]:
    stream_dir = data_dir / stream
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
            if writer.persist(stream, payload):
                inserted += 1
    return seen, inserted


def _replay_reconciliations(data_dir: Path, writer: PersistenceWriter) -> tuple[int, int]:
    directory = data_dir / "predictions" / "reconciliations"
    seen = 0
    inserted = 0
    if not directory.exists():
        return seen, inserted

    for path in sorted(directory.glob("*.jsonl")):
        for raw in load_jsonl_rows(path):
            if not isinstance(raw, dict):
                continue
            payload: dict[str, Any] = {str(key): value for key, value in raw.items()}
            key = str(payload.get("prediction_key") or "").strip()
            if not key:
                continue
            if payload.get("prediction_id") is None:
                payload["prediction_id"] = stable_prediction_id(key)
            seen += 1
            if writer.persist("prediction_reconciliation", payload):
                inserted += 1
    return seen, inserted


def hydrate_persistence_from_jsonl(data_dir: Path) -> dict[str, int]:
    """Replay durable ledgers into the reconstructable SQLite query/index cache."""

    db_path = data_dir / "persistence" / "app.sqlite"
    # One schema migration and connection for the entire rebuild; bounded
    # commits avoid holding an enormous write transaction on large ledgers.
    with PersistenceWriter(db_path, commit_every=1_000) as writer:
        prediction_seen, prediction_inserted = _replay_stream(data_dir, "predictions", writer)
        signal_seen, signal_inserted = _replay_stream(data_dir, "signals", writer)
        reconciliation_seen, reconciliation_inserted = _replay_reconciliations(data_dir, writer)
    return {
        "prediction_rows_seen": prediction_seen,
        "prediction_rows_inserted": prediction_inserted,
        "signal_rows_seen": signal_seen,
        "signal_rows_inserted": signal_inserted,
        "reconciliation_rows_seen": reconciliation_seen,
        "reconciliation_rows_inserted": reconciliation_inserted,
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
