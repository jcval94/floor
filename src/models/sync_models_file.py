from __future__ import annotations

import argparse
import hashlib
import json
import pickle
from datetime import datetime, timezone
from pathlib import Path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_champion_json(models_dir: Path, task: str) -> dict:
    champion_path = models_dir / f"{task}_champion.json"
    payload = json.loads(champion_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Invalid champion payload for task={task}: expected dict")
    return payload


def _persist_models_file(models_file_dir: Path, task: str, payload: dict) -> None:
    models_file_dir.mkdir(parents=True, exist_ok=True)
    pkl_path = models_file_dir / f"{task}_champion.pkl"
    manifest_path = models_file_dir / f"{task}_champion.manifest.json"
    with pkl_path.open("wb") as fh:
        pickle.dump(payload, fh, protocol=pickle.HIGHEST_PROTOCOL)
    manifest = {
        "task": task,
        "format": "pkl",
        "file_name": pkl_path.name,
        "sha256": _sha256_file(pkl_path),
        "model_name": payload.get("model_name"),
        "model_version": payload.get("version"),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def sync_champions(models_dir: Path, models_file_dir: Path, tasks: list[str]) -> None:
    for task in tasks:
        payload = _load_champion_json(models_dir=models_dir, task=task)
        _persist_models_file(models_file_dir=models_file_dir, task=task, payload=payload)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync champion JSON artifacts into models_file pickle+manifest files.")
    parser.add_argument("--models-dir", default="data/training/models", help="Directory with *_champion.json artifacts")
    parser.add_argument("--models-file-dir", default="data/training/models_file", help="Output directory for pkl+manifest files")
    parser.add_argument("--tasks", default="d1,w1,q1,value,timing", help="Comma-separated task names")
    args = parser.parse_args()

    tasks = [part.strip() for part in str(args.tasks).split(",") if part.strip()]
    sync_champions(models_dir=Path(args.models_dir), models_file_dir=Path(args.models_file_dir), tasks=tasks)


if __name__ == "__main__":
    main()
