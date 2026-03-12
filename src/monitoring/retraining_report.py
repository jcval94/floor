from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def build_retraining_report(decision: dict, inputs_snapshot: dict, config_snapshot: dict) -> dict:
    m3_value = (((decision.get("components", {}).get("m3_value_timing_drift", {})).get("value_drift", {})).get("state", "GREEN"))
    m3_timing = (((decision.get("components", {}).get("m3_value_timing_drift", {})).get("timing_drift", {})).get("state", "GREEN"))
    return {
        "as_of": datetime.now(tz=timezone.utc).isoformat(),
        "decision": decision,
        "executive_summary": decision.get("executive_explanation", ""),
        "technical_summary": decision.get("technical_explanation", ""),
        "m3_summary": {
            "m3_value_drift_state": m3_value,
            "m3_timing_drift_state": m3_timing,
            "m3_traffic_light": decision.get("m3_traffic_light", "GREEN"),
            "m3_retraining_decision": decision.get("m3_retraining_decision", "SKIP_M3_RETRAIN"),
        },
        "impact_if_not_retrained": decision.get("impact_if_not_retrained", ""),
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
