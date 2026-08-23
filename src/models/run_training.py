from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import pickle
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from floor.persistence_db import persist_payload
from models.dataset_summary import summarize_modelable_rows
from models.select_champion import select_and_persist_champion
from models.tasks import normalize_model_tasks
from models.train_timing_models import train_floor_week_m3_timing_model
from models.train_value_models import train_floor_m3_value_model

logger = logging.getLogger(__name__)
M3_TASKS = ("value", "timing")
HORIZON_TASKS = {"d1", "w1", "q1"}


def _load_dataset(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
        rows = payload["rows"]
    elif isinstance(payload, list):
        rows = payload
    else:
        raise ValueError("Unsupported dataset payload; expected rows list")
    logger.info("[training] loaded dataset path=%s rows=%s", path, len(rows))
    return rows


def _split_rows(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Keep validation and test separate; run_training never consumes test for selection."""
    train = [row for row in rows if row.get("split") == "train"]
    validation = [row for row in rows if row.get("split") == "validation"]
    if not train:
        raise ValueError("Training dataset has no explicit train split")
    if not validation:
        raise ValueError("Training dataset has no explicit validation split")
    logger.info("[training] split train=%s validation=%s", len(train), len(validation))
    return train, validation


def _resolve_persistence_db_path(
    persistence_db_path: Path | None,
    output_dir: Path,
) -> Path:
    if persistence_db_path is not None:
        return persistence_db_path
    floor_data_dir = os.environ.get("FLOOR_DATA_DIR")
    if floor_data_dir:
        return Path(floor_data_dir) / "persistence" / "app.sqlite"
    return output_dir.parent / "persistence" / "app.sqlite"


def _audit_event(
    *,
    db_path: Path,
    task: str,
    training_mode: str,
    action: str,
    model_name: str,
    model_version: str,
    retrained: bool,
    selection: dict | None,
    artifact_payload: dict | None,
    metrics_path: Path,
    dataset_path: Path,
    output_dir: Path,
) -> None:
    params = artifact_payload.get("params", {}) if isinstance(artifact_payload, dict) else {}
    tuning_summary = params.get("tuning_summary") if isinstance(params, dict) else None
    hyperparameter_grid = params.get("hyperparameter_grid") if isinstance(params, dict) else None
    persist_payload(
        db_path,
        "model_training_cycle",
        {
            "as_of": datetime.now(timezone.utc).isoformat(),
            "task": task,
            "training_mode": training_mode,
            "action": action,
            "champion_decision": (selection or {}).get("decision"),
            "model_name": model_name,
            "model_version": model_version,
            "retrained": retrained,
            "previous_champion_path": (selection or {}).get("previous_champion_path"),
            "previous_champion_version": (selection or {}).get("previous_champion_version"),
            "new_champion_path": (selection or {}).get("champion_path"),
            "challenger_path": (selection or {}).get("challenger_path"),
            "metrics_path": str(metrics_path),
            "dataset_path": str(dataset_path),
            "output_dir": str(output_dir),
            "cv_enabled": bool((tuning_summary or {}).get("cv_enabled", False)),
            "cv_folds": (tuning_summary or {}).get("folds"),
            "hyperparameter_grid": hyperparameter_grid,
            "tuning_summary": tuning_summary,
        },
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _build_model_file_manifest(task: str, artifact_payload: dict, pkl_path: Path) -> dict:
    selection = artifact_payload.get("selection", {})
    model_version = artifact_payload.get("version")
    return {
        "task": task,
        "format": "pkl",
        "file_name": pkl_path.name,
        "sha256": _sha256_file(pkl_path),
        "model_name": artifact_payload.get("model_name"),
        "model_version": model_version,
        "version": model_version,
        "scoring_version": selection.get("scoring_version"),
        "selection_decision": selection.get("decision"),
        "selection_objective": selection.get("objective"),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _persist_winning_model_file(
    task: str,
    models_file_dir: Path,
    artifact_payload: dict,
) -> Path:
    models_file_dir.mkdir(parents=True, exist_ok=True)
    out_path = models_file_dir / f"{task}_champion.pkl"
    with out_path.open("wb") as fh:
        pickle.dump(artifact_payload, fh, protocol=pickle.HIGHEST_PROTOCOL)
    manifest = _build_model_file_manifest(task, artifact_payload, out_path)
    (models_file_dir / f"{task}_champion.manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return out_path


def _load_champion_payload(models_dir: Path, task: str) -> dict | None:
    path = models_dir / f"{task}_champion.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _sync_models_file_champion(task: str, models_dir: Path, models_file_dir: Path) -> None:
    payload = _load_champion_payload(models_dir, task)
    if payload is None:
        raise RuntimeError(f"Cannot sync missing champion payload for task={task}")
    _persist_winning_model_file(task, models_file_dir, payload)


def _normalize_m3_tasks(tasks: str | list[str] | tuple[str, ...] | None) -> list[str]:
    if tasks is None:
        return list(M3_TASKS)
    normalized = normalize_model_tasks(tasks)
    forbidden = [task for task in normalized if task in HORIZON_TASKS]
    if forbidden:
        raise ValueError(
            "models.run_training is m3-only; d1/w1/q1 must be trained via "
            "models.train_classic_horizons. Unsupported here: " + ",".join(forbidden)
        )
    return [task for task in normalized if task in M3_TASKS]


def run_training(
    dataset_path: Path,
    output_dir: Path,
    version: str = "v1",
    tasks: str | list[str] | tuple[str, ...] | None = None,
    training_mode: str = "standard",
    persistence_db_path: Path | None = None,
) -> dict:
    rows = _load_dataset(dataset_path)
    dataset_summary = summarize_modelable_rows(rows)
    train, validation = _split_rows(rows)
    selected_tasks = _normalize_m3_tasks(tasks)
    if not selected_tasks:
        raise ValueError("No m3 tasks requested; use value, timing, or m3")

    models_dir = output_dir / "models"
    models_file_dir = output_dir / "models_file"
    metrics_dir = output_dir / "metrics"
    models_dir.mkdir(parents=True, exist_ok=True)
    metrics_dir.mkdir(parents=True, exist_ok=True)

    selection: dict[str, dict] = {}
    trained_payloads: dict[str, dict] = {}
    metrics_payload: dict[str, object] = {
        "tasks": selected_tasks,
        "dataset_summary": dataset_summary,
        "training_mode": training_mode,
        "test_holdout_used_for_selection": False,
    }

    if "value" in selected_tasks:
        value_artifact = train_floor_m3_value_model(
            train,
            validation,
            model_name="m3_value_linear",
            version=version,
            training_mode=training_mode,
        )
        value_payload = asdict(value_artifact)
        value_payload["dataset_summary"] = dataset_summary
        selection["value"] = select_and_persist_champion(value_payload, models_dir, task="value")
        metrics_payload["value"] = value_artifact.metrics
        trained_payloads["value"] = value_payload

    if "timing" in selected_tasks:
        timing_artifact = train_floor_week_m3_timing_model(
            train,
            validation,
            model_name="m3_timing_multiclass",
            version=version,
            training_mode=training_mode,
        )
        timing_payload = asdict(timing_artifact)
        timing_payload["dataset_summary"] = dataset_summary
        selection["timing"] = select_and_persist_champion(timing_payload, models_dir, task="timing")
        metrics_payload["timing"] = timing_artifact.metrics
        metrics_payload["forecast_contract"] = {
            "floor_week_m3_best_class": timing_artifact.best_class,
            "floor_week_m3_top3": timing_artifact.top3,
        }
        trained_payloads["timing"] = timing_payload

    for task in selected_tasks:
        decision = selection[task].get("decision")
        if training_mode == "retrain" and decision in {"promote", "promote_first"}:
            champion_payload = _load_champion_payload(models_dir, task)
            if champion_payload is None:
                raise RuntimeError(f"Champion disappeared after selection task={task}")
            _persist_winning_model_file(task, models_file_dir, champion_payload)
        if training_mode in {"manual", "retrain", "renewal"}:
            _sync_models_file_champion(task, models_dir, models_file_dir)

    metrics_payload["selection"] = selection
    metrics_path = metrics_dir / f"training_metrics_{version}.json"
    metrics_path.write_text(
        json.dumps(metrics_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    db_path = _resolve_persistence_db_path(persistence_db_path, output_dir)
    for task in M3_TASKS:
        if task in selected_tasks:
            artifact = trained_payloads[task]
            _audit_event(
                db_path=db_path,
                task=task,
                training_mode=training_mode,
                action="TRAINED",
                model_name=str(artifact.get("model_name", f"m3_{task}")),
                model_version=str(artifact.get("version", version)),
                retrained=training_mode == "retrain",
                selection=selection[task],
                artifact_payload=artifact,
                metrics_path=metrics_path,
                dataset_path=dataset_path,
                output_dir=output_dir,
            )
        else:
            _audit_event(
                db_path=db_path,
                task=task,
                training_mode=training_mode,
                action="NOT_TRAINED",
                model_name=f"m3_{task}",
                model_version=version,
                retrained=False,
                selection=None,
                artifact_payload=None,
                metrics_path=metrics_path,
                dataset_path=dataset_path,
                output_dir=output_dir,
            )

    result: dict[str, object] = {
        "metrics_path": str(metrics_path),
        "tasks": selected_tasks,
        "training_mode": training_mode,
        "models_file_dir": str(models_file_dir),
    }
    result.update(selection)
    return result


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    parser = argparse.ArgumentParser(
        description="Train m3 value/timing models with champion/challenger selection"
    )
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output-dir", default="data/training")
    parser.add_argument("--version", default="v1")
    parser.add_argument("--tasks", default="value,timing", help="Only value,timing (or m3 alias) are supported")
    parser.add_argument(
        "--training-mode",
        default="standard",
        choices=["standard", "retrain", "manual", "renewal"],
    )
    parser.add_argument("--persistence-db", default="")
    args = parser.parse_args()
    db_path = Path(args.persistence_db) if args.persistence_db else None
    result = run_training(
        Path(args.dataset),
        Path(args.output_dir),
        version=args.version,
        tasks=args.tasks,
        training_mode=args.training_mode,
        persistence_db_path=db_path,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
