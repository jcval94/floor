from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from floor.persistence_db import persist_payload


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def run(models_dir: Path, db_path: Path) -> int:
    persisted = 0
    as_of = datetime.now(timezone.utc).isoformat()
    for path in sorted(models_dir.glob("*_competition.json")):
        payload = _load(path)
        horizon = str(payload.get("horizon") or "")
        version = str(payload.get("version") or "")
        selected = str(payload.get("selected_model_id") or "")
        candidates = payload.get("candidates", [])
        if not isinstance(candidates, list):
            continue
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            row = {
                **candidate,
                "as_of": as_of,
                "horizon": horizon or candidate.get("horizon"),
                "version": version or candidate.get("version"),
                "is_champion": str(candidate.get("model_id")) == selected,
                "source_artifact": str(path),
            }
            persist_payload(db_path, "model_competition", row)
            persisted += 1
    return persisted


def main() -> None:
    parser = argparse.ArgumentParser(description="Persist per-model horizon competition results into app.sqlite")
    parser.add_argument("--models-dir", default="data/training/models", help="Directory containing *_competition.json files")
    parser.add_argument("--db", default="data/persistence/app.sqlite", help="SQLite persistence DB path")
    args = parser.parse_args()

    count = run(Path(args.models_dir), Path(args.db))
    print(f"persisted_model_competition_rows={count}")


if __name__ == "__main__":
    main()
