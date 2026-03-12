from __future__ import annotations


def generate_weekly_report(week_id: str, daily_reports: list[dict]) -> dict:
    pnl_values = [float(report.get("summary", {}).get("pnl", 0.0)) for report in daily_reports]
    win_rate_values = [float(report.get("summary", {}).get("win_rate", 0.0)) for report in daily_reports]

    total_pnl = sum(pnl_values)
    average_win_rate = sum(win_rate_values) / len(win_rate_values) if win_rate_values else 0.0
    best_day_pnl = max(pnl_values) if pnl_values else 0.0
    worst_day_pnl = min(pnl_values) if pnl_values else 0.0
    total_incidents = sum(int(report.get("summary", {}).get("incident_count", 0)) for report in daily_reports)

    return {
        "week_id": week_id,
        "days": [report.get("date") for report in daily_reports],
        "summary": {
            "days_count": len(daily_reports),
            "total_pnl": total_pnl,
            "average_win_rate": average_win_rate,
            "best_day_pnl": best_day_pnl,
            "worst_day_pnl": worst_day_pnl,
            "incident_count": total_incidents,
            "status": "alert" if total_incidents > 0 else "ok",
        },
        "daily_reports": daily_reports,
    }
