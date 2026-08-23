from __future__ import annotations

import json
import logging
import math
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
    """Score m3 value on scale-free quantities only.

    The old score mixed dollar MAE with probabilities and therefore favored or
    punished models depending on the price mix of the validation universe. The
    new score is entirely relative to price and explicitly rewards the desired
    20% floor-breach calibration.
    """

    return (
        float(metrics.get("pinball_loss_delta", 999.0))
        + float(metrics.get("mae_delta", 999.0))
        + 2.0 * float(metrics.get("breach_rate_error", 999.0))
        + 0.25 * float(metrics.get("calibration_error", 999.0))
        + 0.10 * (1.0 - float(metrics.get("temporal_stability", 0.0)))
    )


def _timing_score(metrics: dict) -> float:
    uniform_log_loss = float(metrics.get("uniform_log_loss", math.log(13)))
    log_loss = float(metrics.get("log_loss", 999.0))
    normalized_log_loss = log_loss / max(uniform_log_loss, 1e-9)
    negative_skill_penalty = max(0.0, -float(metrics.get("log_loss_skill", -999.0)))
    return (
        (1.0 - float(metrics.get("top1_accuracy", 0.0)))
        + (1.0 - float(metrics.get("top3_accuracy", 0.0)))
        + normalized_log_loss
        + float(metrics.get("brier_score", 999.0))
        + float(metrics.get("expected_week_distance", 999.0)) / 13.0
        + float(metrics.get("calibration_error", 999.0))
        + 0.50 * float(metrics.get("abstention_rate", 1.0))
        + negative_skill_penalty
    )


def _horizon_score(metrics: dict) -> float:
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


def _incompatible_champion_schema(task: str, artifact: dict) -> bool:
    """Return True when old/new scores are not statistically comparable."""
    params = artifact.get("params")
    metrics = artifact.get("metrics")
    if not isinstance(params, dict):
        return task in {"value", "timing"}
    if not isinstance(metrics, dict):
        return task in {"value", "timing"}
    if task == "value":
        return not (
            int(params.get("schema_version") or 0) == 2
            and params.get("target_space") == "relative_floor_delta"
            and all(
                key in metrics
                for key in ("pinball_loss_delta", "mae_delta", "breach_rate_error")
            )
        )
    if task == "timing":
        return not (
            int(params.get("schema_version") or 0) == 2
            and params.get("model_type") == "multinomial_logistic"
            and int(params.get("class_count") or 0) == 13
            and all(key in metrics for key in ("log_loss_skill", "abstention_rate"))
        )
    return False


def select_and_persist_champion(new_artifact: object, registry_dir: Path, task: str) -> dict:
    registry_dir.mkdir(parents=True, exist_ok=True)
    payload = _to_dict(new_artifact)
    now = datetime.utcnow().isoformat() + "Z"

    champion_path = registry_dir / f"{task}_champion.json"
    challenger_path = registry_dir / f"{task}_challenger_{now.replace(':', '').replace('-', '')}.json"

    existing = _load_json(champion_path)
    new_score = _task_score(task, payload["metrics"])
    old_score: float | None = None

    decision = "promote_first"
    reason = "No champion exists; bootstrap champion with first valid artifact."
    previous_champion_version = None
    archived_path = None
    archived: Path | None = None

    if existing is not None:
        previous_champion_version = existing.get("version")
        if _incompatible_champion_schema(task, existing):
            decision = "promote"
            reason = (
                "Existing champion uses an incompatible/deprecated statistical quality schema; "
                "archive it and promote the first valid artifact without comparing scores."
            )
            archived = registry_dir / f"{task}_champion_archived_{now.replace(':', '').replace('-', '')}.json"
            archived_path = str(archived)
            logger.warning(
                "[champion-selection] task=%s force quality-schema migration old_version=%s new_score=%.6f",
                task,
                previous_champion_version,
                new_score,
            )
        else:
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
        "scoring_version": "m3-quality-v3" if task in {"value", "timing"} else "m3-v1",
        "evaluated_at": now,
        "new_score": new_score,
        "existing_score": old_score,
        "objective": "minimize_scale_free_out_of_time_error",
    }
    try:
        if decision == "promote":
            if existing is None or archived is None:
                raise RuntimeError("Promotion decision requires an existing champion artifact.")
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
