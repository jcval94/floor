from __future__ import annotations

from datetime import date, datetime, timedelta

from floor.calendar import is_us_market_holiday


DAY_NAME_ES = {
    0: "lunes",
    1: "martes",
    2: "miércoles",
    3: "jueves",
    4: "viernes",
    5: "sábado",
    6: "domingo",
}


def _next_business_day(d: date) -> date:
    x = d + timedelta(days=1)
    while x.weekday() >= 5 or is_us_market_holiday(x):
        x += timedelta(days=1)
    return x


def _forward_business_days(base_date: date, n: int) -> list[date]:
    out: list[date] = []
    x = base_date
    for _ in range(n):
        x = _next_business_day(x)
        out.append(x)
    return out


def add_relative_business_day_labels(base_date: date, relative_day: int | str | None) -> dict:
    if relative_day is None:
        return {"date": None, "day_name": None}
    try:
        n = int(relative_day)
    except (TypeError, ValueError):
        return {"date": None, "day_name": None}
    if n <= 0:
        return {"date": None, "day_name": None}

    d = base_date
    for _ in range(n):
        d = _next_business_day(d)
    return {"date": d.isoformat(), "day_name": DAY_NAME_ES[d.weekday()]}


def add_relative_market_week_labels(base_date: date, relative_week: int | str | None, sessions_per_week: int = 5) -> dict:
    if relative_week is None:
        return {"start_date": None, "end_date": None, "label_human": None, "week_index": None}
    try:
        week_idx = int(relative_week)
    except (TypeError, ValueError):
        return {"start_date": None, "end_date": None, "label_human": None, "week_index": None}
    if week_idx <= 0:
        return {"start_date": None, "end_date": None, "label_human": None, "week_index": None}

    days = _forward_business_days(base_date, week_idx * sessions_per_week)
    start = (week_idx - 1) * sessions_per_week
    end = min(len(days), start + sessions_per_week)
    if start >= len(days):
        return {"start_date": None, "end_date": None, "label_human": None, "week_index": week_idx}
    chunk = days[start:end]
    s = chunk[0].isoformat()
    e = chunk[-1].isoformat()
    return {
        "start_date": s,
        "end_date": e,
        "week_index": week_idx,
        "label_human": f"Semana {week_idx:02d} ({s} → {e}) dentro del horizonte m3 (1..13 semanas bursátiles relativas)",
    }


def render_horizon_time_labels(forecast_row: dict, as_of: datetime) -> dict:
    row = dict(forecast_row)
    base = as_of.date()

    w_floor = add_relative_business_day_labels(base, row.get("floor_day_w1"))
    w_ceil = add_relative_business_day_labels(base, row.get("ceiling_day_w1"))
    q_floor = add_relative_business_day_labels(base, row.get("floor_day_q1"))
    q_ceil = add_relative_business_day_labels(base, row.get("ceiling_day_q1"))

    row["floor_date_w1"] = w_floor["date"]
    row["floor_day_name_w1"] = w_floor["day_name"]
    row["ceiling_date_w1"] = w_ceil["date"]
    row["ceiling_day_name_w1"] = w_ceil["day_name"]

    row["floor_date_q1"] = q_floor["date"]
    row["floor_day_name_q1"] = q_floor["day_name"]
    row["ceiling_date_q1"] = q_ceil["date"]
    row["ceiling_day_name_q1"] = q_ceil["day_name"]

    m3 = add_relative_market_week_labels(base, row.get("floor_week_m3"))
    row["floor_week_m3_start_date"] = m3["start_date"]
    row["floor_week_m3_end_date"] = m3["end_date"]
    row["floor_week_m3_label_human"] = m3["label_human"]
    return row
