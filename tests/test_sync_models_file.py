from __future__ import annotations

import json
import pickle
from pathlib import Path

from models.sync_models_file import sync_champions


def test_sync_champions_exports_pickle_and_manifest(tmp_path: Path) -> None:
    models_dir = tmp_path / "models"
    models_file_dir = tmp_path / "models_file"
    models_dir.mkdir(parents=True, exist_ok=True)

    for task in ("d1", "w1", "q1", "value", "timing"):
        (models_dir / f"{task}_champion.json").write_text(
            json.dumps({"model_name": f"{task}_model", "version": f"{task}-v1", "params": {}, "metrics": {}}),
            encoding="utf-8",
        )

    sync_champions(models_dir=models_dir, models_file_dir=models_file_dir, tasks=["d1", "w1", "q1", "value", "timing"])

    for task in ("d1", "w1", "q1", "value", "timing"):
        pkl_path = models_file_dir / f"{task}_champion.pkl"
        manifest_path = models_file_dir / f"{task}_champion.manifest.json"
        assert pkl_path.exists()
        assert manifest_path.exists()
        payload = pickle.loads(pkl_path.read_bytes())
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert payload["version"] == f"{task}-v1"
        assert manifest["task"] == task
        assert manifest["model_version"] == f"{task}-v1"
