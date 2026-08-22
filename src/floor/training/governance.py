from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

BLOCK_REASON = (
    "Automatic champion replacement is disabled until train/serve parity, "
    "promotion controls, and retraining data gates are validated."
)


def block_automatic_retrain(summary_path: Path) -> dict[str, Any]:
    if not summary_path.exists():
        raise RuntimeError(f"Retraining governance refused: missing summary at {summary_path}")

    try:
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Retraining governance refused: invalid summary at {summary_path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Retraining governance refused: summary must be a JSON object")

    requested = [
        str(task).strip()
        for task in payload.get("tasks_for_auto_retrain", [])
        if str(task).strip()
    ]
    payload["tasks_for_auto_retrain_requested"] = requested
    payload["tasks_for_auto_retrain"] = []
    payload["auto_retrain_enabled"] = False
    payload["auto_retrain_block_reason"] = BLOCK_REASON

    models = payload.get("models")
    if isinstance(models, dict):
        for record in models.values():
            if not isinstance(record, dict):
                continue
            requested_flag = bool(record.get("auto_retrain", False))
            record["auto_retrain_requested"] = requested_flag
            record["auto_retrain"] = False

    summary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply retraining promotion safety gates")
    parser.add_argument("--summary", default="data/training/review_summary_latest.json")
    args = parser.parse_args()

    try:
        payload = block_automatic_retrain(Path(args.summary))
    except RuntimeError as exc:
        print(json.dumps({"status": "CRITICAL", "error": str(exc)}, ensure_ascii=False))
        return 1

    print(
        json.dumps(
            {
                "status": "OK",
                "auto_retrain_enabled": payload["auto_retrain_enabled"],
                "tasks_for_auto_retrain_requested": payload["tasks_for_auto_retrain_requested"],
                "tasks_for_auto_retrain": payload["tasks_for_auto_retrain"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
