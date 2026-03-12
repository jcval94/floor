from __future__ import annotations


def generate_model_report(
    date: str,
    model_health: dict,
    drift_alerts: list[dict],
    retrain_decisions: list[dict],
) -> dict:
    drift_count = len(drift_alerts)
    retrain_count = len(retrain_decisions)

    return {
        "date": date,
        "model_health": model_health,
        "drift_alerts": drift_alerts,
        "retrain_decisions": retrain_decisions,
        "summary": {
            "drift_alert_count": drift_count,
            "retrain_decision_count": retrain_count,
            "status": "alert" if drift_count > 0 else "ok",
        },
        "status": "alert" if drift_count > 0 else "ok",
    }
