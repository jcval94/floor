from __future__ import annotations

import argparse
import json
import logging
import os
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


def _load_dataset(path: Path) -> list[dict]:
    logger.info("[training] loading dataset path=%s", path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "rows" in payload:
        rows = payload["rows"]
    elif isinstance(payload, list):
        rows = payload
    else:
        raise ValueError("Unsupported dataset payload")

    if not isinstance(rows, list):
        raise ValueError("Unsupported dataset rows; expected a list")
    logger.info("[training] loaded rows=%s", len(rows))
    if rows:
        logger.info("[training] sample row=%s", rows[0])
    return rows


def _split_rows(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    train = [r for r in rows if r.get("split") == "train"]
    valid = [r for r in rows if r.get("split") in {"validation", "test"}]
    if not train:
        train = rows[: int(len(rows) * 0.7)]
    if not valid:
        valid = rows[int(len(rows) * 0.7) :]
    logger.info("[training] split train=%s valid=%s", len(train), len(valid))
    return train, valid


def _resolve_persistence_db_path(persistence_db_path: Path | None, output_dir: Path) -> Path:
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

    payload = {
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
    }
    persist_payload(db_path, "model_training_cycle", payload)


def run_training(
    dataset_path: Path,
    output_dir: Path,
    version: str = "v1",
    tasks: str | list[str] | tuple[str, ...] | None = None,
    training_mode: str = "standard",
    persistence_db_path: Path | None = None,
) -> dict:
    try:
        rows = _load_dataset(dataset_path)
        dataset_summary = summarize_modelable_rows(rows)
        train, valid = _split_rows(rows)
        selected_tasks = normalize_model_tasks(tasks)

        models_dir = output_dir / "models"
        metrics_dir = output_dir / "metrics"
        models_dir.mkdir(parents=True, exist_ok=True)
        metrics_dir.mkdir(parents=True, exist_ok=True)

        selection: dict[str, dict] = {}
        metrics_payload: dict[str, object] = {
            "tasks": selected_tasks,
            "dataset_summary": dataset_summary,
            "training_mode": training_mode,
        }
        trained_payloads: dict[str, dict] = {}

        if "value" in selected_tasks:
            logger.info("[training] training value model version=%s", version)
            value_artifact = train_floor_m3_value_model(
                train,
                valid,
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
            logger.info("[training] training timing model version=%s", version)
            timing_artifact = train_floor_week_m3_timing_model(
                train,
                valid,
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

        metrics_payload["selection"] = selection
        metrics_path = metrics_dir / f"training_metrics_{version}.json"
        metrics_path.write_text(json.dumps(metrics_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("[training] metrics saved path=%s", metrics_path)

        db_path = _resolve_persistence_db_path(persistence_db_path, output_dir=output_dir)
        for task in ["value", "timing"]:
            if task in selected_tasks:
                art = trained_payloads.get(task, {})
                sel = selection.get(task)
                _audit_event(
                    db_path=db_path,
                    task=task,
                    training_mode=training_mode,
                    action="TRAINED",
                    model_name=str(art.get("model_name", f"m3_{task}")),
                    model_version=str(art.get("version", version)),
                    retrained=training_mode == "retrain",
                    selection=sel,
                    artifact_payload=art,
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

        result: dict[str, object] = {"metrics_path": str(metrics_path), "tasks": selected_tasks, "training_mode": training_mode}
        result.update(selection)
        return result
    except Exception as exc:
        logger.exception("[training] run_training failed dataset=%s output=%s error=%s", dataset_path, output_dir, exc)
        raise


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    parser = argparse.ArgumentParser(description="Train m3 value and timing models with champion/challenger selection")
    parser.add_argument("--dataset", required=True, help="Path to modelable dataset JSON")
    parser.add_argument("--output-dir", default="data/training", help="Output directory for artifacts and metrics")
    parser.add_argument("--version", default="v1", help="Training version tag")
    parser.add_argument("--tasks", default="value,timing", help="Comma-separated model tasks to train")
    parser.add_argument(
        "--training-mode",
        default="standard",
        choices=["standard", "retrain", "manual", "renewal"],
        help="Training trigger/mode. Hyperparameter CV runs only in retrain mode.",
    )
    parser.add_argument("--persistence-db", default="", help="Optional SQLite persistence DB path")
    args = parser.parse_args()

    try:
        db_path = Path(args.persistence_db) if args.persistence_db else None
        result = run_training(
            Path(args.dataset),
            Path(args.output_dir),
            version=args.version,
            tasks=args.tasks,
            training_mode=args.training_mode,
            persistence_db_path=db_path,
        )
        logger.info("[training] run complete result=%s", result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as exc:
        logger.exception("[training] CLI failed: %s", exc)
        raise


if __name__ == "__main__":
    main()
