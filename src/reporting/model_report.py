from __future__ import annotations


def generate_model_report(
    date: str,
    model_health: dict,
    drift_alerts: list[dict],
    retrain_decisions: list[dict],
    m3_forecast_stability: dict | None = None,
) -> dict:
    drift_count = len(drift_alerts)
    retrain_count = len(retrain_decisions)
    m3_forecast_stability = m3_forecast_stability or {}

    return {
        "date": date,
        "model_health": model_health,
        "drift_alerts": drift_alerts,
        "retrain_decisions": retrain_decisions,
        "3m_downside_window": {
            "stability": {
                "floor_m3_delta_avg": m3_forecast_stability.get("floor_m3_delta_avg"),
                "floor_week_m3_shift_rate": m3_forecast_stability.get("floor_week_m3_shift_rate"),
                "top3_jaccard_avg": m3_forecast_stability.get("top3_jaccard_avg"),
                "material_change_count": m3_forecast_stability.get("material_change_count"),
            }
        },
        "summary": {
            "drift_alert_count": drift_count,
            "retrain_decision_count": retrain_count,
            "status": "alert" if drift_count > 0 else "ok",
        },
        "status": "alert" if drift_count > 0 else "ok",
    }
