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
  Mapping is computed from elapsed trading-session seconds since the first bar of
  the day (fine-grained label) and then collapsed to the 5 operational buckets.
- floor_day_w1 / ceiling_day_w1:
  relative business-day index (1..5) inside next 5 trading days where extrema occur.
- floor_day_q1 / ceiling_day_q1:
  relative business-day index (1..10) inside next 10 trading days where extrema occur.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
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


def _rows_by_symbol_and_day(rows: list[dict]) -> dict[str, dict[datetime.date, list[dict]]]:
    grouped: dict[str, dict[datetime.date, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        ts = _to_datetime(row["timestamp"])
        grouped[row["symbol"]][ts.date()].append(row)
    for symbol_days in grouped.values():
        for day_rows in symbol_days.values():
            day_rows.sort(key=lambda x: _to_datetime(x["timestamp"]))
    return grouped


def _relative_day_of_extreme(days: list[datetime.date], per_day_rows: dict[datetime.date, list[dict]], kind: str) -> int | None:
    if not days:
        return None
    best_value: float | None = None
    best_day: datetime.date | None = None
    for idx, day in enumerate(days, start=1):
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

        def future_days(n: int) -> list[datetime.date]:
            start = day_idx + 1
            end = min(len(days), start + n)
            return days[start:end]

        horizon_map = {"d1": 1, "w1": 5, "q1": 10}
        for horizon, n_days in horizon_map.items():
            fdays = future_days(n_days)
            if not fdays:
                row[f"floor_{horizon}"] = None
                row[f"ceiling_{horizon}"] = None
                row[f"realized_floor_{horizon}"] = None
                row[f"realized_ceiling_{horizon}"] = None
                row[f"forward_return_{horizon}"] = None
                row[f"floor_breach_flag_{horizon}"] = None
                row[f"ceiling_reach_flag_{horizon}"] = None
                row[f"realized_range_{horizon}"] = None
                continue

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

        # d1 temporal buckets with finer event timestamp then bucket mapping.
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

        # Relative business-day targets, not fixed weekdays.
        row["floor_day_w1"] = _relative_day_of_extreme(future_days(5), grouped[symbol], "floor")
        row["ceiling_day_w1"] = _relative_day_of_extreme(future_days(5), grouped[symbol], "ceiling")
        row["floor_day_q1"] = _relative_day_of_extreme(future_days(10), grouped[symbol], "floor")
        row["ceiling_day_q1"] = _relative_day_of_extreme(future_days(10), grouped[symbol], "ceiling")

    return rows
