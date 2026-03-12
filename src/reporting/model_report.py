from __future__ import annotations


def generate_model_report(date: str, model_health: dict, drift_alerts: list[dict], retrain_decisions: list[dict]) -> dict:
    return {
        "date": date,
        "model_health": model_health,
        "drift_alerts": drift_alerts,
        "retrain_decisions": retrain_decisions,
        "status": "alert" if drift_alerts else "ok",
    }
