from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path


def _to_dict(obj: object) -> dict:
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    if isinstance(obj, dict):
        return obj
    raise TypeError("Unsupported artifact type")


def _load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


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


def select_and_persist_champion(new_artifact: object, registry_dir: Path, task: str) -> dict:
    registry_dir.mkdir(parents=True, exist_ok=True)
    payload = _to_dict(new_artifact)
    now = datetime.utcnow().isoformat() + "Z"

    champion_path = registry_dir / f"{task}_champion.json"
    challenger_path = registry_dir / f"{task}_challenger_{now.replace(':', '').replace('-', '')}.json"

    existing = _load_json(champion_path)
    new_score = _value_score(payload["metrics"]) if task == "value" else _timing_score(payload["metrics"])

    decision = "promote_first"
    reason = "No champion exists; bootstrap champion with first valid artifact."

    if existing is not None:
        old_score = _value_score(existing["metrics"]) if task == "value" else _timing_score(existing["metrics"])
        if new_score + 1e-9 < old_score:
            decision = "promote"
            reason = f"New artifact improved score from {old_score:.6f} to {new_score:.6f}."
            archived = registry_dir / f"{task}_champion_archived_{now.replace(':', '').replace('-', '')}.json"
            archived.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            decision = "challenger_only"
            reason = f"Existing champion kept (score {old_score:.6f} <= {new_score:.6f})."

    payload["selection"] = {"decision": decision, "reason": reason, "scoring_version": "m3-v1", "evaluated_at": now}
    challenger_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if decision in {"promote_first", "promote"}:
        champion_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "decision": decision,
        "reason": reason,
        "champion_path": str(champion_path),
        "challenger_path": str(challenger_path),
    }
