"""Leakage-safe label engineering for floor/ceiling targets.

All forward targets require the full requested number of future trading
sessions. Incomplete right-censored horizons are emitted as None and are never
eligible for model training.

Each row also carries target_end_date_<horizon> and
horizon_complete_<horizon> metadata. The dataset splitter uses the m3
(maximum 65-session) target end to purge rows whose labels would cross a split
boundary.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Iterable

D1_BUCKETS = ("OPEN", "OPEN_PLUS_2H", "OPEN_PLUS_4H", "OPEN_PLUS_6H", "CLOSE")
HORIZON_SESSIONS = {"d1": 1, "w1": 5, "q1": 10, "m3": 65}


def _to_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


def _bucket_from_event(event_ts: datetime, session_open: datetime) -> str:
    elapsed = (event_ts - session_open).total_seconds()
    if elapsed <= 0:
        return "OPEN"
    if elapsed <= 2 * 3600:
        return "OPEN_PLUS_2H"
    if elapsed <= 4 * 3600:
        return "OPEN_PLUS_4H"
    if elapsed <= 6 * 3600:
        return "OPEN_PLUS_6H"
    return "CLOSE"


def _rows_by_symbol_and_day(rows: list[dict]) -> dict[str, dict[date, list[dict]]]:
    grouped: dict[str, dict[date, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        ts = _to_datetime(row["timestamp"])
        grouped[str(row["symbol"])][ts.date()].append(row)
    for symbol_days in grouped.values():
        for day_rows in symbol_days.values():
            day_rows.sort(key=lambda x: _to_datetime(x["timestamp"]))
    return grouped


def _exact_future_days(days: list[date], day_idx: int, sessions: int) -> list[date]:
    start = day_idx + 1
    end = start + sessions
    if end > len(days):
        return []
    out = days[start:end]
    return out if len(out) == sessions else []


def _relative_day_of_extreme(
    days: list[date],
    per_day_rows: dict[date, list[dict]],
    kind: str,
) -> int | None:
    if not days:
        return None
    best_value: float | None = None
    best_day: date | None = None
    for day in days:
        day_rows = per_day_rows[day]
        if kind == "floor":
            value = min(float(r["low"]) for r in day_rows)
            better = best_value is None or value < best_value
        else:
            value = max(float(r["high"]) for r in day_rows)
            better = best_value is None or value > best_value
        if better:
            best_value = value
            best_day = day
    if best_day is None:
        return None
    return days.index(best_day) + 1


def _set_standard_missing(row: dict, horizon: str) -> None:
    for key in (
        f"floor_{horizon}",
        f"ceiling_{horizon}",
        f"realized_floor_{horizon}",
        f"realized_ceiling_{horizon}",
        f"forward_return_{horizon}",
        f"floor_breach_flag_{horizon}",
        f"ceiling_reach_flag_{horizon}",
        f"realized_range_{horizon}",
    ):
        row[key] = None


def _label_standard_horizon(
    row: dict,
    grouped: dict[str, dict[date, list[dict]]],
    symbol: str,
    fdays: list[date],
    horizon: str,
) -> None:
    expected = HORIZON_SESSIONS[horizon]
    if len(fdays) != expected:
        _set_standard_missing(row, horizon)
        row[f"horizon_complete_{horizon}"] = False
        row[f"target_end_date_{horizon}"] = None
        return

    future_rows = [r for d in fdays for r in grouped[symbol][d]]
    realized_floor = min(float(r["low"]) for r in future_rows)
    realized_ceiling = max(float(r["high"]) for r in future_rows)
    end_close = float(grouped[symbol][fdays[-1]][-1]["close"])
    close = float(row["close"])

    row[f"floor_{horizon}"] = realized_floor
    row[f"ceiling_{horizon}"] = realized_ceiling
    row[f"realized_floor_{horizon}"] = realized_floor
    row[f"realized_ceiling_{horizon}"] = realized_ceiling
    row[f"forward_return_{horizon}"] = (end_close / close) - 1.0 if close else None
    row[f"realized_range_{horizon}"] = realized_ceiling - realized_floor
    row[f"horizon_complete_{horizon}"] = True
    row[f"target_end_date_{horizon}"] = fdays[-1].isoformat()

    ai_floor = row.get(f"ai_floor_{horizon}")
    ai_ceiling = row.get(f"ai_ceiling_{horizon}")
    row[f"floor_breach_flag_{horizon}"] = (
        None if ai_floor is None else int(realized_floor <= float(ai_floor))
    )
    row[f"ceiling_reach_flag_{horizon}"] = (
        None if ai_ceiling is None else int(realized_ceiling >= float(ai_ceiling))
    )


def _set_m3_missing(row: dict) -> None:
    for key in (
        "floor_m3",
        "realized_floor_m3",
        "floor_delta_m3",
        "floor_week_m3",
        "forward_return_m3",
        "realized_range_m3",
        "floor_breach_flag_m3",
        "floor_week_m3_start_date",
        "floor_week_m3_end_date",
    ):
        row[key] = None
    row["horizon_complete_m3"] = False
    row["target_end_date_m3"] = None


def _label_m3_horizon(
    row: dict,
    grouped: dict[str, dict[date, list[dict]]],
    symbol: str,
    days: list[date],
    day_idx: int,
) -> None:
    forward_days = _exact_future_days(days, day_idx, HORIZON_SESSIONS["m3"])
    if len(forward_days) != HORIZON_SESSIONS["m3"]:
        _set_m3_missing(row)
        return

    week_chunks = [
        forward_days[i : i + 5]
        for i in range(0, HORIZON_SESSIONS["m3"], 5)
    ]
    if len(week_chunks) != 13 or any(len(chunk) != 5 for chunk in week_chunks):
        _set_m3_missing(row)
        return

    future_rows = [r for d in forward_days for r in grouped[symbol][d]]
    realized_floor = min(float(r["low"]) for r in future_rows)
    realized_ceiling = max(float(r["high"]) for r in future_rows)
    end_close = float(grouped[symbol][forward_days[-1]][-1]["close"])
    close = float(row["close"])

    week_floor_values = [
        min(float(r["low"]) for d in week for r in grouped[symbol][d])
        for week in week_chunks
    ]
    best_week_idx = min(range(len(week_floor_values)), key=lambda i: week_floor_values[i])
    best_week = week_chunks[best_week_idx]

    row["floor_m3"] = realized_floor
    row["realized_floor_m3"] = realized_floor
    row["floor_delta_m3"] = (
        max(0.0, min(0.95, (close - realized_floor) / close))
        if close > 0
        else None
    )
    row["floor_week_m3"] = best_week_idx + 1
    row["forward_return_m3"] = (end_close / close) - 1.0 if close else None
    row["realized_range_m3"] = realized_ceiling - realized_floor
    row["floor_week_m3_start_date"] = best_week[0].isoformat()
    row["floor_week_m3_end_date"] = best_week[-1].isoformat()
    row["horizon_complete_m3"] = True
    row["target_end_date_m3"] = forward_days[-1].isoformat()

    ai_floor = row.get("ai_floor_m3")
    row["floor_breach_flag_m3"] = (
        None if ai_floor is None else int(realized_floor <= float(ai_floor))
    )


def _has_intraday_resolution(day_rows: list[dict]) -> bool:
    timestamps = {_to_datetime(r["timestamp"]) for r in day_rows}
    return len(timestamps) >= 2


def build_labels(feature_rows: Iterable[dict]) -> list[dict]:
    rows = list(feature_rows)
    grouped = _rows_by_symbol_and_day(rows)

    for row in rows:
        symbol = str(row["symbol"])
        current_ts = _to_datetime(row["timestamp"])
        current_day = current_ts.date()
        days = sorted(grouped[symbol].keys())
        try:
            day_idx = days.index(current_day)
        except ValueError:
            continue

        d1_days = _exact_future_days(days, day_idx, 1)
        w1_days = _exact_future_days(days, day_idx, 5)
        q1_days = _exact_future_days(days, day_idx, 10)

        _label_standard_horizon(row, grouped, symbol, d1_days, "d1")
        _label_standard_horizon(row, grouped, symbol, w1_days, "w1")
        _label_standard_horizon(row, grouped, symbol, q1_days, "q1")
        _label_m3_horizon(row, grouped, symbol, days, day_idx)

        # Intraday timing is unavailable from one daily OHLC bar. Do not fabricate
        # OPEN/+2h/+4h/+6h/CLOSE labels from daily timestamps.
        if d1_days:
            d1_rows = grouped[symbol][d1_days[0]]
            if _has_intraday_resolution(d1_rows):
                session_open = _to_datetime(d1_rows[0]["timestamp"])
                floor_event = min(d1_rows, key=lambda r: float(r["low"]))
                ceil_event = max(d1_rows, key=lambda r: float(r["high"]))
                row["floor_time_bucket_d1"] = _bucket_from_event(
                    _to_datetime(floor_event["timestamp"]), session_open
                )
                row["ceiling_time_bucket_d1"] = _bucket_from_event(
                    _to_datetime(ceil_event["timestamp"]), session_open
                )
                row["d1_timing_available"] = True
            else:
                row["floor_time_bucket_d1"] = None
                row["ceiling_time_bucket_d1"] = None
                row["d1_timing_available"] = False
        else:
            row["floor_time_bucket_d1"] = None
            row["ceiling_time_bucket_d1"] = None
            row["d1_timing_available"] = False

        row["floor_day_w1"] = _relative_day_of_extreme(
            w1_days, grouped[symbol], "floor"
        )
        row["ceiling_day_w1"] = _relative_day_of_extreme(
            w1_days, grouped[symbol], "ceiling"
        )
        row["floor_day_q1"] = _relative_day_of_extreme(
            q1_days, grouped[symbol], "floor"
        )
        row["ceiling_day_q1"] = _relative_day_of_extreme(
            q1_days, grouped[symbol], "ceiling"
        )

    return rows