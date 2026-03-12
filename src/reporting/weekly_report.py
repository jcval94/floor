from __future__ import annotations


def generate_weekly_report(week_id: str, daily_reports: list[dict]) -> dict:
    total_pnl = sum(float(d.get("summary", {}).get("pnl", 0.0)) for d in daily_reports)
    avg_win_rate = 0.0
    if daily_reports:
        avg_win_rate = sum(float(d.get("summary", {}).get("win_rate", 0.0)) for d in daily_reports) / len(daily_reports)

    return {
        "week_id": week_id,
        "days": [d.get("date") for d in daily_reports],
        "total_pnl": total_pnl,
        "average_win_rate": avg_win_rate,
        "daily_reports": daily_reports,
    }
