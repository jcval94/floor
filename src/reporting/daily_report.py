from __future__ import annotations


def generate_daily_report(
    date: str,
    session_metrics: dict,
    risk_changes: list[dict],
    incidents: list[dict],
    m3_window: dict | None = None,
) -> dict:
    close_metrics = session_metrics.get("CLOSE", {})
    session_count = len(session_metrics)
    incident_count = len(incidents)
    m3_window = m3_window or {}

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
        "3m_downside_window": {
            "floor_m3": m3_window.get("floor_m3"),
            "floor_week_m3": m3_window.get("floor_week_m3"),
            "floor_week_m3_start_date": m3_window.get("floor_week_m3_start_date"),
            "floor_week_m3_end_date": m3_window.get("floor_week_m3_end_date"),
            "floor_week_m3_confidence": m3_window.get("floor_week_m3_confidence"),
            "floor_week_m3_top3": m3_window.get("floor_week_m3_top3", []),
            "m3_stability_vs_previous": m3_window.get("m3_stability_vs_previous", {}),
        },
    }
