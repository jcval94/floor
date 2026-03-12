from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def build_retraining_report(decision: dict, inputs_snapshot: dict, config_snapshot: dict) -> dict:
    return {
        "as_of": datetime.now(tz=timezone.utc).isoformat(),
        "decision": decision,
        "inputs_snapshot": inputs_snapshot,
        "config_snapshot": config_snapshot,
    }


def save_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def append_history(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(report, ensure_ascii=False) + "\n")
