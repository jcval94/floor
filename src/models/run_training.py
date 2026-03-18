from __future__ import annotations

import argparse
import json
import hashlib
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






def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _build_model_file_manifest(task: str, artifact_payload: dict, pkl_path: Path) -> dict:
    selection = artifact_payload.get("selection", {}) if isinstance(artifact_payload, dict) else {}
    return {
        "task": task,
        "format": "pkl",
        "file_name": pkl_path.name,
        "sha256": _sha256_file(pkl_path),
        "model_name": artifact_payload.get("model_name") if isinstance(artifact_payload, dict) else None,
        "model_version": artifact_payload.get("version") if isinstance(artifact_payload, dict) else None,
        "scoring_version": selection.get("scoring_version"),
        "selection_decision": selection.get("decision"),
        "selection_objective": selection.get("objective"),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

def _persist_winning_model_file(task: str, models_file_dir: Path, artifact_payload: dict) -> Path:
    models_file_dir.mkdir(parents=True, exist_ok=True)
    out_path = models_file_dir / f"{task}_champion.pkl"
    manifest_path = models_file_dir / f"{task}_champion.manifest.json"
    logger.info(
        "[training] persisting models_file artifact task=%s pkl=%s manifest=%s",
        task,
        out_path,
        manifest_path,
    )
    with out_path.open("wb") as fh:
        pickle.dump(artifact_payload, fh, protocol=pickle.HIGHEST_PROTOCOL)

    manifest = _build_model_file_manifest(task=task, artifact_payload=artifact_payload, pkl_path=out_path)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    pkl_size = out_path.stat().st_size
    manifest_size = manifest_path.stat().st_size

    logger.info(
        "[training] persisted winning model file task=%s path=%s format=pkl manifest=%s sha256=%s pkl_bytes=%s manifest_bytes=%s",
        task,
        out_path,
        manifest_path,
        manifest["sha256"],
        pkl_size,
        manifest_size,
    )
    return out_path


def _load_champion_payload(models_dir: Path, task: str) -> dict | None:
    champion_path = models_dir / f"{task}_champion.json"
    if not champion_path.exists():
        return None
    try:
        payload = json.loads(champion_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("[training] champion json unreadable task=%s path=%s", task, champion_path)
        return None
    return payload if isinstance(payload, dict) else None


def _sync_models_file_champion(task: str, models_dir: Path, models_file_dir: Path) -> None:
    logger.info("[training] syncing champion into models_file task=%s", task)
    champion_payload = _load_champion_payload(models_dir=models_dir, task=task)
    if champion_payload is None:
        logger.warning("[training] skip models_file sync task=%s reason=missing_champion_payload", task)
        return
    _persist_winning_model_file(task, models_file_dir, champion_payload)



def _horizon_defaults(task: str) -> dict[str, float | str]:
    if task == "d1":
        return {
            "move_base": 1.2,
            "move_vol_mult": 0.4,
            "bias_mult": 0.15,
            "breach_base": 0.35,
            "breach_vol_mult": 0.15,
            "expected_feature": "rel_strength_20",
            "expected_mult": 0.5,
            "time_floor_positive": 2.0,
            "time_floor_negative": 4.0,
            "time_ceiling_positive": 6.0,
            "time_ceiling_negative": 8.0,
        }
    if task == "w1":
        return {
            "move_base": 2.2,
            "move_vol_mult": 0.5,
            "bias_mult": 0.1,
            "breach_base": 0.42,
            "breach_vol_mult": 0.18,
            "expected_feature": "rel_strength_20",
            "expected_mult": 0.8,
            "time_floor_positive": 2.0,
            "time_floor_negative": 1.0,
            "time_ceiling_positive": 5.0,
            "time_ceiling_negative": 4.0,
        }
    if task == "q1":
        return {
            "move_base": 3.6,
            "move_vol_mult": 0.6,
            "bias_mult": 0.1,
            "breach_base": 0.5,
            "breach_vol_mult": 0.15,
            "expected_feature": "momentum_20",
            "expected_mult": 0.9,
            "time_floor_positive": 3.0,
            "time_floor_negative": 2.0,
            "time_ceiling_positive": 10.0,
            "time_ceiling_negative": 8.0,
        }
    raise ValueError(f"Unsupported horizon task: {task}")


def _train_horizon_model(task: str, rows: list[dict], version: str, dataset_summary: dict) -> dict:
    cfg = _horizon_defaults(task)
    usable = [r for r in rows if r.get("close") not in (None, "")]
    if not usable:
        metrics = {"mae_proxy": 999.0, "breach_rate_proxy": 1.0, "temporal_stability": 0.0, "rows_used": 0}
    else:
        close_vals = [float(r.get("close") or 0.0) for r in usable]
        mean_close = sum(close_vals) / len(close_vals)
        mean_abs_dev = sum(abs(v - mean_close) for v in close_vals) / len(close_vals)
        coverage = len(usable) / max(1, len(rows))
        metrics = {
            "mae_proxy": round(mean_abs_dev / max(mean_close, 1.0), 6),
            "breach_rate_proxy": round(max(0.01, min(0.99, 0.2 + (1 - coverage) * 0.2)), 6),
            "temporal_stability": round(max(0.0, min(1.0, coverage)), 6),
            "rows_used": len(usable),
        }

    return {
        "model_name": f"{task}_heuristic_v1",
        "horizon": task,
        "target": f"{task}_band",
        "version": version,
        "params": cfg,
        "metrics": metrics,
        "dataset_summary": dataset_summary,
    }


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
        models_file_dir = output_dir / "models_file"
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

        for horizon_task in ["d1", "w1", "q1"]:
            if horizon_task not in selected_tasks:
                continue
            logger.info("[training] training horizon model task=%s version=%s", horizon_task, version)
            horizon_payload = _train_horizon_model(horizon_task, valid, version=version, dataset_summary=dataset_summary)
            selection[horizon_task] = select_and_persist_champion(horizon_payload, models_dir, task=horizon_task)
            logger.info(
                "[training] champion selection task=%s decision=%s champion=%s challenger=%s",
                horizon_task,
                selection[horizon_task].get("decision"),
                selection[horizon_task].get("champion_path"),
                selection[horizon_task].get("challenger_path"),
            )
            metrics_payload[horizon_task] = horizon_payload["metrics"]
            trained_payloads[horizon_task] = horizon_payload
            if training_mode == "retrain" and selection[horizon_task].get("decision") in {"promote", "promote_first"}:
                _persist_winning_model_file(horizon_task, models_file_dir, horizon_payload)
            else:
                logger.info(
                    "[training] skip winning model file update task=%s training_mode=%s decision=%s",
                    horizon_task,
                    training_mode,
                    selection[horizon_task].get("decision"),
                )
            if training_mode in {"manual", "retrain", "renewal"}:
                _sync_models_file_champion(task=horizon_task, models_dir=models_dir, models_file_dir=models_file_dir)

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
            logger.info(
                "[training] champion selection task=value decision=%s champion=%s challenger=%s",
                selection["value"].get("decision"),
                selection["value"].get("champion_path"),
                selection["value"].get("challenger_path"),
            )
            metrics_payload["value"] = value_artifact.metrics
            trained_payloads["value"] = value_payload
            if training_mode == "retrain" and selection["value"].get("decision") in {"promote", "promote_first"}:
                _persist_winning_model_file("value", models_file_dir, value_payload)
            else:
                logger.info(
                    "[training] skip winning model file update task=value training_mode=%s decision=%s",
                    training_mode,
                    selection["value"].get("decision"),
                )
            if training_mode in {"manual", "retrain", "renewal"}:
                _sync_models_file_champion(task="value", models_dir=models_dir, models_file_dir=models_file_dir)

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
            logger.info(
                "[training] champion selection task=timing decision=%s champion=%s challenger=%s",
                selection["timing"].get("decision"),
                selection["timing"].get("champion_path"),
                selection["timing"].get("challenger_path"),
            )
            metrics_payload["timing"] = timing_artifact.metrics
            metrics_payload["forecast_contract"] = {
                "floor_week_m3_best_class": timing_artifact.best_class,
                "floor_week_m3_top3": timing_artifact.top3,
            }
            trained_payloads["timing"] = timing_payload
            if training_mode == "retrain" and selection["timing"].get("decision") in {"promote", "promote_first"}:
                _persist_winning_model_file("timing", models_file_dir, timing_payload)
            else:
                logger.info(
                    "[training] skip winning model file update task=timing training_mode=%s decision=%s",
                    training_mode,
                    selection["timing"].get("decision"),
                )
            if training_mode in {"manual", "retrain", "renewal"}:
                _sync_models_file_champion(task="timing", models_dir=models_dir, models_file_dir=models_file_dir)

        metrics_payload["selection"] = selection
        metrics_path = metrics_dir / f"training_metrics_{version}.json"
        metrics_path.write_text(json.dumps(metrics_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("[training] metrics saved path=%s bytes=%s", metrics_path, metrics_path.stat().st_size)

        db_path = _resolve_persistence_db_path(persistence_db_path, output_dir=output_dir)
        for task in ["d1", "w1", "q1", "value", "timing"]:
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

        result: dict[str, object] = {"metrics_path": str(metrics_path), "tasks": selected_tasks, "training_mode": training_mode, "models_file_dir": str(models_file_dir)}
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
    parser.add_argument("--tasks", default="d1,w1,q1,value,timing", help="Comma-separated model tasks to train")
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
