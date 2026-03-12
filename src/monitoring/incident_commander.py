from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

REQUIRED_INTRADAY_EVENTS = {"OPEN", "OPEN_PLUS_2H", "OPEN_PLUS_4H", "OPEN_PLUS_6H", "CLOSE"}


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_workflow_markers(path: Path, day: str) -> list[dict]:
    if not path.exists():
        return []

    markers: list[dict] = []
    for file in sorted(path.glob("*.json")):
        try:
            payload = json.loads(file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if payload.get("day") == day:
            markers.append(payload)
    return markers


def _repo_status() -> dict:
    status = subprocess.run(["git", "status", "--short"], capture_output=True, text=True, check=False)
    return {
        "dirty": bool(status.stdout.strip()),
        "raw": status.stdout.strip().splitlines(),
    }


def _severity_from_incidents(incidents: list[dict]) -> str:
    if any(x.get("severity") == "critical" for x in incidents):
        return "SEV1"
    if any(x.get("severity") == "high" for x in incidents):
        return "SEV2"
    if any(x.get("severity") == "medium" for x in incidents):
        return "SEV3"
    return "SEV4"


def _latest_healthy_run_id(markers: list[dict]) -> str:
    by_run: dict[str, set[str]] = {}
    for marker in markers:
        run_id = str(marker.get("run_id") or "")
        if not run_id:
            continue
        if marker.get("kind") != "intraday":
            continue
        event = str(marker.get("event") or "")
        if not event:
            continue
        by_run.setdefault(run_id, set()).add(event)

    healthy = [run_id for run_id, events in by_run.items() if REQUIRED_INTRADAY_EVENTS.issubset(events)]
    return healthy[-1] if healthy else "UNKNOWN"


def build_incident_report(day: str, daily_report: dict, workflow_markers: list[dict], repo_status: dict) -> dict:
    incidents = daily_report.get("incidents", [])
    severity = _severity_from_incidents(incidents)

    intraday_events = {str(x.get("event")) for x in workflow_markers if x.get("kind") == "intraday" and x.get("event")}
    missing_events = sorted(REQUIRED_INTRADAY_EVENTS - intraday_events)

    symptom = "No existe snapshot intraday OPEN y faltan eventos de checkpoint en workflow_runs."
    cause = (
        "El orquestador no garantiza cobertura de todos los checkpoints intradía; "
        "se ejecutó solo una parte de eventos y no existe control de completitud por sesión."
    )

    execution_impact = any(i.get("area") == "execution" for i in incidents)
    strategy_impact = any(i.get("area") == "strategy" for i in incidents)
    risk_impact = any(i.get("area") == "risk" for i in incidents)

    status = "ESCALATE" if severity in {"SEV1", "SEV2"} else "INVESTIGATING"

    immediate_fix = [
        "Forzar corrida manual de checkpoints intradía faltantes para la fecha afectada.",
        "Bloquear publicación de métricas EOD como completas cuando falten snapshots críticos.",
        "Notificar incidente a on-call con severidad y alcance por componente.",
    ]
    permanent_fix = [
        "Registrar run_id/workflow en cada marker de workflow para trazabilidad y último run sano.",
        "Agregar chequeo automático de completitud intradía (OPEN, OPEN+2H, OPEN+4H, OPEN+6H, CLOSE) antes de EOD.",
        "Agregar comando de incident review reproducible con RCA estructurado y salida JSON auditada.",
    ]

    tests_to_add = [
        "Test para validar que mark_run persiste run_id/workflow en snapshots.",
        "Test para validar clasificación de severidad y estado de escalamiento.",
        "Test para validar identificación de eventos intradía faltantes y root cause asociado.",
    ]

    latest_healthy = _latest_healthy_run_id(workflow_markers)

    return {
        "status": status,
        "severity": severity,
        "summary": {
            "date": day,
            "latest_healthy_run_id": latest_healthy,
            "symptom": symptom,
            "root_cause_hypothesis": cause,
        },
        "root_cause_analysis": {
            "symptom": symptom,
            "cause": cause,
            "missing_intraday_events": missing_events,
            "workflow_events_seen": sorted(intraday_events),
            "repo_dirty": repo_status.get("dirty", False),
        },
        "impact": {
            "forecasts": "PARCIAL" if missing_events else "OK",
            "strategies": "AFECTADO" if strategy_impact else "NO_EVIDENCIA",
            "paper_trading": "AFECTADO" if execution_impact else "NO_EVIDENCIA",
            "notifications": "RIESGO_DE_ALERTA_TARDIA" if missing_events else "OK",
            "risk_controls": "AFECTADO" if risk_impact else "NO_EVIDENCIA",
        },
        "immediate_fix": immediate_fix,
        "permanent_fix": permanent_fix,
        "tests_to_add": tests_to_add,
        "files_updated": [
            "src/utils/workflow_guards.py",
            "src/monitoring/incident_commander.py",
            "tests/test_incident_commander.py",
            "docs/release_checklist.md",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Incident commander review")
    parser.add_argument("--day", required=True, help="Session day in YYYY-MM-DD")
    parser.add_argument("--daily-report", required=True, help="Path to daily close report json")
    parser.add_argument("--workflow-dir", default="data/snapshots/workflow_runs", help="Workflow markers directory")
    parser.add_argument("--output", required=True, help="Output incident report json")
    args = parser.parse_args()

    daily_report = _load_json(Path(args.daily_report))
    markers = _load_workflow_markers(Path(args.workflow_dir), args.day)
    repo_status = _repo_status()

    report = build_incident_report(args.day, daily_report, markers, repo_status)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
