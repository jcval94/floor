"""Label engineering for floor/ceiling targets.

Target definitions implemented in this module:
- floor_{h} / ceiling_{h}:
  extrema (min low / max high) observed in the forward horizon h.
- realized_floor_{h} / realized_ceiling_{h}:
  realized extrema in the same forward horizon; identical numeric value to
  floor_{h}/ceiling_{h}, but kept as explicit evaluation columns.
- forward_return_{h}:
  close(t + h_end) / close(t) - 1.
- floor_breach_flag_{h}:
  1 if realized_floor_{h} <= ai_floor_{h}; 0 otherwise; None if ai_floor_{h} missing.
- ceiling_reach_flag_{h}:
  1 if realized_ceiling_{h} >= ai_ceiling_{h}; 0 otherwise; None if ai_ceiling_{h} missing.
- realized_range_{h}:
  realized_ceiling_{h} - realized_floor_{h}.

Temporal target definitions:
- floor_time_bucket_d1 / ceiling_time_bucket_d1:
  exact intraday timestamp of floor/ceiling event in next trading day mapped to
  operational buckets {OPEN, OPEN_PLUS_2H, OPEN_PLUS_4H, OPEN_PLUS_6H, CLOSE}.
- floor_day_w1 / ceiling_day_w1:
  relative business-day index (1..5) inside next 5 trading days where extrema occur.
- floor_day_q1 / ceiling_day_q1:
  relative business-day index (1..10) inside next 10 trading days where extrema occur.
- floor_week_m3:
  relative market-week index (1..13) in the forward horizon. Weeks are built as
  contiguous chunks of up to 5 future trading sessions. If a week has holidays or
  partial data, it keeps fewer sessions. Tie-break rule for identical minima across
  weeks is stable: choose the earliest relative week index.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Iterable

D1_BUCKETS = ("OPEN", "OPEN_PLUS_2H", "OPEN_PLUS_4H", "OPEN_PLUS_6H", "CLOSE")


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
        grouped[row["symbol"]][ts.date()].append(row)
    for symbol_days in grouped.values():
        for day_rows in symbol_days.values():
            day_rows.sort(key=lambda x: _to_datetime(x["timestamp"]))
    return grouped


def _relative_day_of_extreme(days: list[date], per_day_rows: dict[date, list[dict]], kind: str) -> int | None:
    if not days:
        return None
    best_value: float | None = None
    best_day: date | None = None
    for day in days:
        day_rows = per_day_rows[day]
        if kind == "floor":
            value = min(r["low"] for r in day_rows)
            better = best_value is None or value < best_value
        else:
            value = max(r["high"] for r in day_rows)
            better = best_value is None or value > best_value
        if better:
            best_value = value
            best_day = day
    if best_day is None:
        return None
    return days.index(best_day) + 1


def _future_week_chunks(days: list[date], start_idx: int, weeks: int = 13, sessions_per_week: int = 5) -> list[list[date]]:
    forward_days = days[start_idx + 1 : start_idx + 1 + (weeks * sessions_per_week)]
    chunks: list[list[date]] = []
    for i in range(0, len(forward_days), sessions_per_week):
        chunk = forward_days[i : i + sessions_per_week]
        if chunk:
            chunks.append(chunk)
    return chunks


def _label_standard_horizon(row: dict, grouped: dict[str, dict[date, list[dict]]], symbol: str, fdays: list[date], horizon: str) -> None:
    if not fdays:
        row[f"floor_{horizon}"] = None
        row[f"ceiling_{horizon}"] = None
        row[f"realized_floor_{horizon}"] = None
        row[f"realized_ceiling_{horizon}"] = None
        row[f"forward_return_{horizon}"] = None
        row[f"floor_breach_flag_{horizon}"] = None
        row[f"ceiling_reach_flag_{horizon}"] = None
        row[f"realized_range_{horizon}"] = None
        return

    future_rows = [r for d in fdays for r in grouped[symbol][d]]
    realized_floor = min(r["low"] for r in future_rows)
    realized_ceiling = max(r["high"] for r in future_rows)
    end_close = grouped[symbol][fdays[-1]][-1]["close"]
    row[f"floor_{horizon}"] = realized_floor
    row[f"ceiling_{horizon}"] = realized_ceiling
    row[f"realized_floor_{horizon}"] = realized_floor
    row[f"realized_ceiling_{horizon}"] = realized_ceiling
    row[f"forward_return_{horizon}"] = (end_close / row["close"]) - 1.0
    row[f"realized_range_{horizon}"] = realized_ceiling - realized_floor

    ai_floor = row.get(f"ai_floor_{horizon}")
    ai_ceiling = row.get(f"ai_ceiling_{horizon}")
    row[f"floor_breach_flag_{horizon}"] = None if ai_floor is None else int(realized_floor <= ai_floor)
    row[f"ceiling_reach_flag_{horizon}"] = None if ai_ceiling is None else int(realized_ceiling >= ai_ceiling)


def _label_m3_horizon(row: dict, grouped: dict[str, dict[date, list[dict]]], symbol: str, days: list[date], day_idx: int) -> None:
    week_chunks = _future_week_chunks(days, day_idx, weeks=13, sessions_per_week=5)
    if not week_chunks:
        row["floor_m3"] = None
        row["realized_floor_m3"] = None
        row["floor_week_m3"] = None
        row["forward_return_m3"] = None
        row["realized_range_m3"] = None
        row["floor_breach_flag_m3"] = None
        row["floor_week_m3_start_date"] = None
        row["floor_week_m3_end_date"] = None
        return

    forward_days = [d for week in week_chunks for d in week]
    future_rows = [r for d in forward_days for r in grouped[symbol][d]]
    realized_floor = min(r["low"] for r in future_rows)
    realized_ceiling = max(r["high"] for r in future_rows)
    end_close = grouped[symbol][forward_days[-1]][-1]["close"]

    week_floor_values = [min(r["low"] for d in week for r in grouped[symbol][d]) for week in week_chunks]
    best_week_idx = min(range(len(week_floor_values)), key=lambda i: week_floor_values[i])
    # Stable tie-break: earliest relative week index due to min() first-match behavior.
    best_week = week_chunks[best_week_idx]

    row["floor_m3"] = realized_floor
    row["realized_floor_m3"] = realized_floor
    row["floor_week_m3"] = best_week_idx + 1
    row["forward_return_m3"] = (end_close / row["close"]) - 1.0
    row["realized_range_m3"] = realized_ceiling - realized_floor
    row["floor_week_m3_start_date"] = best_week[0].isoformat()
    row["floor_week_m3_end_date"] = best_week[-1].isoformat()

    ai_floor = row.get("ai_floor_m3")
    row["floor_breach_flag_m3"] = None if ai_floor is None else int(realized_floor <= ai_floor)


def build_labels(feature_rows: Iterable[dict]) -> list[dict]:
    rows = list(feature_rows)
    grouped = _rows_by_symbol_and_day(rows)

    for row in rows:
        symbol = row["symbol"]
        current_ts = _to_datetime(row["timestamp"])
        current_day = current_ts.date()
        days = sorted(grouped[symbol].keys())
        try:
            day_idx = days.index(current_day)
        except ValueError:
            continue

        def future_days(n: int) -> list[date]:
            start = day_idx + 1
            end = min(len(days), start + n)
            return days[start:end]

        horizon_map = {"d1": 1, "w1": 5, "q1": 10}
        for horizon, n_days in horizon_map.items():
            _label_standard_horizon(row, grouped, symbol, future_days(n_days), horizon)

        _label_m3_horizon(row, grouped, symbol, days, day_idx)

        d1_days = future_days(1)
        if d1_days:
            d1_rows = grouped[symbol][d1_days[0]]
            session_open = _to_datetime(d1_rows[0]["timestamp"])
            floor_event = min(d1_rows, key=lambda r: r["low"])
            ceil_event = max(d1_rows, key=lambda r: r["high"])
            row["floor_time_bucket_d1"] = _bucket_from_event(_to_datetime(floor_event["timestamp"]), session_open)
            row["ceiling_time_bucket_d1"] = _bucket_from_event(_to_datetime(ceil_event["timestamp"]), session_open)
        else:
            row["floor_time_bucket_d1"] = None
            row["ceiling_time_bucket_d1"] = None

        row["floor_day_w1"] = _relative_day_of_extreme(future_days(5), grouped[symbol], "floor")
        row["ceiling_day_w1"] = _relative_day_of_extreme(future_days(5), grouped[symbol], "ceiling")
        row["floor_day_q1"] = _relative_day_of_extreme(future_days(10), grouped[symbol], "floor")
        row["ceiling_day_q1"] = _relative_day_of_extreme(future_days(10), grouped[symbol], "ceiling")

    return rows
