from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Any

from features.feature_builder import build_features
from utils.market_session import ET, checkpoint_times, get_session_info


def _parse_dt(value: object) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError("empty timestamp")
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _session_date(value: object) -> date:
    return _parse_dt(value).astimezone(ET).date()


def session_checkpoint(session_day: date, event: str) -> datetime:
    info = get_session_info(datetime.combine(session_day, datetime.min.time(), tzinfo=ET))
    checkpoints = checkpoint_times(info)
    if event not in checkpoints:
        raise ValueError(f"event {event!r} is not valid for session {session_day}")
    return checkpoints[event]


def aggregate_partial_bar(
    intraday_rows: list[dict[str, Any]],
    session_day: date,
    checkpoint: datetime,
) -> dict[str, Any]:
    """Aggregate only information observable by a checkpoint.

    Yahoo intraday timestamps are bar-start timestamps. A bar is treated as
    completed only when its start is strictly before the checkpoint. At OPEN
    no interval is completed, so only the first regular-session opening print
    is used and OHLC is set to that known price with zero observed volume.
    """
    session_rows = [
        dict(row)
        for row in intraday_rows
        if _session_date(row.get("timestamp")) == session_day
    ]
    session_rows.sort(key=lambda row: _parse_dt(row["timestamp"]))
    if not session_rows:
        raise ValueError(f"no intraday rows for {session_day}")

    first = session_rows[0]
    opening = float(first["open"])
    completed = [
        row
        for row in session_rows
        if _parse_dt(row["timestamp"]).astimezone(ET) < checkpoint
    ]
    if not completed:
        return {
            "timestamp": checkpoint.isoformat(),
            "open": opening,
            "high": opening,
            "low": opening,
            "close": opening,
            "volume": 0.0,
            "source_max_timestamp": None,
        }

    return {
        "timestamp": checkpoint.isoformat(),
        "open": opening,
        "high": max(float(row["high"]) for row in completed),
        "low": min(float(row["low"]) for row in completed),
        "close": float(completed[-1]["close"]),
        "volume": sum(float(row.get("volume", 0.0) or 0.0) for row in completed),
        "source_max_timestamp": str(completed[-1]["timestamp"]),
    }


def _historical_daily_rows(
    daily_rows: list[dict[str, Any]],
    session_day: date,
) -> list[dict[str, Any]]:
    rows = [
        dict(row)
        for row in daily_rows
        if _session_date(row.get("timestamp")) < session_day
    ]
    rows.sort(key=lambda row: _parse_dt(row["timestamp"]))
    return rows


def build_point_in_time_feature_rows(
    *,
    daily_by_symbol: dict[str, list[dict[str, Any]]],
    intraday_by_symbol: dict[str, list[dict[str, Any]]],
    symbols: list[str],
    benchmark_symbol: str,
    session_day: date,
    event: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    checkpoint = session_checkpoint(session_day, event)
    benchmark_symbol = benchmark_symbol.upper()
    requested = sorted(set([*(symbol.upper() for symbol in symbols), benchmark_symbol]))

    partial_by_symbol: dict[str, dict[str, Any]] = {}
    for symbol in requested:
        partial_by_symbol[symbol] = aggregate_partial_bar(
            intraday_by_symbol.get(symbol, []),
            session_day,
            checkpoint,
        )

    benchmark_close = float(partial_by_symbol[benchmark_symbol]["close"])
    latest_features: dict[str, dict[str, Any]] = {}
    source_maxima: dict[str, str | None] = {}
    benchmark_history = _historical_daily_rows(
        daily_by_symbol.get(benchmark_symbol, []), session_day
    )
    if len(benchmark_history) < 66:
        raise RuntimeError(
            f"insufficient point-in-time benchmark history={len(benchmark_history)}"
        )
    benchmark_close_by_day = {
        _session_date(row["timestamp"]).isoformat(): float(row["close"])
        for row in benchmark_history
    }

    for symbol in symbols:
        symbol = symbol.upper()
        history = _historical_daily_rows(daily_by_symbol.get(symbol, []), session_day)
        if len(history) < 66:
            raise RuntimeError(
                f"insufficient point-in-time history symbol={symbol} asset={len(history)}"
            )

        raw_rows: list[dict[str, Any]] = []
        for row in history:
            day_key = _session_date(row["timestamp"]).isoformat()
            raw_rows.append(
                {
                    "timestamp": row["timestamp"],
                    "symbol": symbol,
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row.get("volume", 0.0) or 0.0),
                    "benchmark_close": benchmark_close_by_day.get(day_key),
                }
            )

        partial = partial_by_symbol[symbol]
        raw_rows.append(
            {
                "timestamp": checkpoint.isoformat(),
                "symbol": symbol,
                "open": float(partial["open"]),
                "high": float(partial["high"]),
                "low": float(partial["low"]),
                "close": float(partial["close"]),
                "volume": float(partial["volume"]),
                "benchmark_close": benchmark_close,
            }
        )
        featured = build_features(raw_rows)
        if not featured:
            raise RuntimeError(f"feature builder returned no rows for {symbol}")
        latest = dict(featured[-1])
        if _parse_dt(latest["timestamp"]).astimezone(ET) != checkpoint:
            raise RuntimeError(f"feature timestamp mismatch for {symbol}")
        latest_features[symbol] = latest
        source_maxima[symbol] = partial.get("source_max_timestamp")

    for symbol, source_ts in source_maxima.items():
        if source_ts is None:
            continue
        if _parse_dt(source_ts).astimezone(ET) >= checkpoint:
            raise RuntimeError(
                f"point-in-time leakage symbol={symbol}: source={source_ts} "
                f"checkpoint={checkpoint.isoformat()}"
            )

    ordered = [latest_features[symbol.upper()] for symbol in symbols]
    audit = {
        "session": session_day.isoformat(),
        "event": event,
        "checkpoint": checkpoint.isoformat(),
        "symbols": len(ordered),
        "benchmark": benchmark_symbol,
        "source_max_timestamp_by_symbol": source_maxima,
        "future_data_used": False,
    }
    return ordered, audit


def group_by_symbol(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        symbol = str(row.get("symbol") or "").upper()
        if symbol:
            grouped[symbol].append(dict(row))
    for values in grouped.values():
        values.sort(key=lambda row: _parse_dt(row["timestamp"]))
    return dict(grouped)
