from __future__ import annotations

import json
from pathlib import Path

from monitoring.incident_commander import build_incident_report
from utils.workflow_guards import mark_run


def test_mark_run_persists_run_metadata(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GITHUB_RUN_ID", "12345")
    monkeypatch.setenv("GITHUB_WORKFLOW", "intraday-pipeline")

    marker = mark_run(kind="intraday", data_dir=tmp_path, event="OPEN")
    payload = json.loads(marker.read_text(encoding="utf-8"))

    assert payload["run_id"] == "12345"
    assert payload["workflow"] == "intraday-pipeline"
    assert payload["event"] == "OPEN"


def test_incident_report_escalates_with_high_incidents() -> None:
    daily_report = {
        "incidents": [
            {"id": "INC-ORD-001", "severity": "high", "area": "execution", "issue": "No orders"},
            {"id": "INC-WF-001", "severity": "medium", "area": "orchestration", "issue": "Missing OPEN"},
        ]
    }
    markers = [
        {"kind": "intraday", "day": "2026-03-12", "event": "OPEN_PLUS_6H", "run_id": "r-2"},
        {"kind": "intraday", "day": "2026-03-12", "event": "CLOSE", "run_id": "r-2"},
    ]

    report = build_incident_report("2026-03-12", daily_report, markers, {"dirty": False})

    assert report["severity"] == "SEV2"
    assert report["status"] == "ESCALATE"
    assert "OPEN" in report["root_cause_analysis"]["missing_intraday_events"]
    assert report["impact"]["paper_trading"] == "AFECTADO"
