from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def _to_dict(obj: object) -> dict:
    if is_dataclass(obj):
        if isinstance(obj, type):
            raise TypeError("Unsupported artifact type")
        return asdict(obj)
    if isinstance(obj, dict):
        return obj
    raise TypeError("Unsupported artifact type")


def _load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        logger.warning("[training] champion json unreadable path=%s reason=empty_payload", path)
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("[training] champion json unreadable path=%s error=%s", path, exc)
        return None
    if not isinstance(payload, dict):
        logger.warning("[training] champion json unreadable path=%s reason=payload_not_object", path)
        return None
    return payload


def _write_json_atomic(path: Path, payload: dict, *, task: str) -> None:
    tmp_path = path.with_suffix(path.suffix + f".{os.getpid()}.{time.time_ns()}.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as tmp_file:
            json.dump(payload, tmp_file, ensure_ascii=False, indent=2)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        tmp_path.replace(path)
    except Exception:
        logger.exception(
            "[champion-selection] Failed JSON persistence task=%s path=%s tmp_path=%s",
            task,
            path,
            tmp_path,
        )
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            logger.exception(
                "[champion-selection] Failed cleanup of temporary file task=%s tmp_path=%s",
                task,
                tmp_path,
            )
        raise


def _value_score(metrics: dict) -> float:
    # lower is better
    return (
        metrics.get("pinball_loss", 999)
        + metrics.get("mae_realized_floor", 999)
        + abs(metrics.get("breach_rate", 0.2) - 0.2)
        + metrics.get("calibration_error", 999)
        + (1 - metrics.get("temporal_stability", 0.0))
    )


def _timing_score(metrics: dict) -> float:
    # higher acc and lower losses/dist are better
    return (
        (1 - metrics.get("top1_accuracy", 0.0))
        + (1 - metrics.get("top3_accuracy", 0.0))
        + metrics.get("log_loss", 999)
        + metrics.get("brier_score", 999)
        + metrics.get("expected_week_distance", 999) / 13
        + metrics.get("calibration_error", 999)
    )


def _horizon_score(metrics: dict) -> float:
    # lower is better
    return (
        float(metrics.get("mae_proxy", 999.0))
        + abs(float(metrics.get("breach_rate_proxy", 0.2)) - 0.2)
        + (1 - float(metrics.get("temporal_stability", 0.0)))
    )


def _task_score(task: str, metrics: dict) -> float:
    if task == "value":
        return _value_score(metrics)
    if task == "timing":
        return _timing_score(metrics)
    if task in {"d1", "w1", "q1"}:
        return _horizon_score(metrics)
    raise ValueError(f"Unsupported champion task for scoring: {task}")


def select_and_persist_champion(new_artifact: object, registry_dir: Path, task: str) -> dict:
    registry_dir.mkdir(parents=True, exist_ok=True)
    payload = _to_dict(new_artifact)
    now = datetime.utcnow().isoformat() + "Z"

    champion_path = registry_dir / f"{task}_champion.json"
    challenger_path = registry_dir / f"{task}_challenger_{now.replace(':', '').replace('-', '')}.json"

    existing = _load_json(champion_path)
    new_score = _task_score(task, payload["metrics"])

    decision = "promote_first"
    reason = "No champion exists; bootstrap champion with first valid artifact."
    previous_champion_version = None
    archived_path = None

    if existing is not None:
        previous_champion_version = existing.get("version")
        old_score = _task_score(task, existing["metrics"])
        logger.info(
            "[champion-selection] task=%s old_score=%.6f new_score=%.6f criterion=lower_is_better",
            task,
            old_score,
            new_score,
        )
        if new_score + 1e-9 < old_score:
            decision = "promote"
            reason = f"New artifact improved score from {old_score:.6f} to {new_score:.6f}."
            archived = registry_dir / f"{task}_champion_archived_{now.replace(':', '').replace('-', '')}.json"
            archived_path = str(archived)
        else:
            decision = "challenger_only"
            reason = f"Existing champion kept (score {old_score:.6f} <= {new_score:.6f})."
    else:
        logger.info(
            "[champion-selection] task=%s old_score=none new_score=%.6f criterion=lower_is_better",
            task,
            new_score,
        )

    payload["selection"] = {
        "decision": decision,
        "reason": reason,
        "scoring_version": "m3-v1",
        "evaluated_at": now,
        "new_score": new_score,
        "existing_score": old_score if existing is not None else None,
        "objective": "minimize_weighted_error",
    }
    try:
        if decision == "promote":
            if existing is None:
                raise RuntimeError("Promotion decision requires an existing champion artifact.")
            assert existing is not None
            _write_json_atomic(archived, existing, task=task)
        _write_json_atomic(challenger_path, payload, task=task)
        if decision in {"promote_first", "promote"}:
            _write_json_atomic(champion_path, payload, task=task)
    except Exception:
        logger.error(
            "[champion-selection] Persistence aborted task=%s champion_path=%s challenger_path=%s",
            task,
            champion_path,
            challenger_path,
        )
        raise

    return {
        "decision": decision,
        "reason": reason,
        "champion_path": str(champion_path),
        "challenger_path": str(challenger_path),
        "previous_champion_path": str(champion_path) if existing is not None else None,
        "previous_champion_version": previous_champion_version,
        "archived_champion_path": archived_path,
    }
