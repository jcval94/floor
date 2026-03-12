from __future__ import annotations


def generate_daily_report(date: str, session_metrics: dict, risk_changes: list[dict], incidents: list[dict]) -> dict:
    return {
        "date": date,
        "summary": {
            "sessions": sorted(session_metrics.keys()),
            "pnl": float(session_metrics.get("CLOSE", {}).get("pnl", 0.0)),
            "win_rate": float(session_metrics.get("CLOSE", {}).get("win_rate", 0.0)),
            "max_drawdown": float(session_metrics.get("CLOSE", {}).get("max_drawdown", 0.0)),
        },
        "sessions": session_metrics,
        "risk_changes": risk_changes,
        "incidents": incidents,
    }
