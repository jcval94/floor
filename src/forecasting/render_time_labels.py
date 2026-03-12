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
    return row
