from __future__ import annotations


def generate_daily_report(
    date: str,
    session_metrics: dict,
    risk_changes: list[dict],
    incidents: list[dict],
) -> dict:
    close_metrics = session_metrics.get("CLOSE", {})
    session_count = len(session_metrics)
    incident_count = len(incidents)

    return {
        "date": date,
        "summary": {
            "sessions": sorted(session_metrics.keys()),
            "session_count": session_count,
            "pnl": float(close_metrics.get("pnl", 0.0)),
            "win_rate": float(close_metrics.get("win_rate", 0.0)),
            "max_drawdown": float(close_metrics.get("max_drawdown", 0.0)),
            "risk_changes_count": len(risk_changes),
            "incident_count": incident_count,
            "status": "alert" if incident_count > 0 else "ok",
        },
        "sessions": session_metrics,
        "risk_changes": risk_changes,
        "incidents": incidents,
    }
