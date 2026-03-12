from __future__ import annotations


def generate_weekly_report(week_id: str, daily_reports: list[dict]) -> dict:
    pnl_values = [float(report.get("summary", {}).get("pnl", 0.0)) for report in daily_reports]
    win_rate_values = [float(report.get("summary", {}).get("win_rate", 0.0)) for report in daily_reports]

    total_pnl = sum(pnl_values)
    average_win_rate = sum(win_rate_values) / len(win_rate_values) if win_rate_values else 0.0
    best_day_pnl = max(pnl_values) if pnl_values else 0.0
    worst_day_pnl = min(pnl_values) if pnl_values else 0.0
    total_incidents = sum(int(report.get("summary", {}).get("incident_count", 0)) for report in daily_reports)

    m3_rows = [report.get("3m_downside_window", {}) for report in daily_reports]
    m3_by_ticker: dict[str, dict] = {}
    for row in m3_rows:
        ticker = str(row.get("ticker") or "").strip()
        if not ticker:
            continue
        m3_by_ticker[ticker] = {
            "floor_m3": row.get("floor_m3"),
            "floor_week_m3": row.get("floor_week_m3"),
            "floor_week_m3_start_date": row.get("floor_week_m3_start_date"),
            "floor_week_m3_end_date": row.get("floor_week_m3_end_date"),
            "floor_week_m3_confidence": row.get("floor_week_m3_confidence"),
            "floor_week_m3_top3": row.get("floor_week_m3_top3", []),
            "m3_stability_vs_previous": row.get("m3_stability_vs_previous", {}),
        }

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
        "3m_downside_window": {
            "by_ticker": m3_by_ticker,
            "ticker_count": len(m3_by_ticker),
        },
        "daily_reports": daily_reports,
    }
