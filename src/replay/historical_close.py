from __future__ import annotations

from datetime import date
from typing import Any

from features.feature_builder import build_features
from replay.point_in_time import _parse_dt, _session_date, session_checkpoint


def _daily_bar_for_session(rows: list[dict[str, Any]], session_day: date) -> dict[str, Any]:
    matches = [dict(row) for row in rows if _session_date(row.get("timestamp")) == session_day]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one daily bar for {session_day}, got={len(matches)}")
    return matches[0]


def build_historical_close_feature_rows(
    *,
    daily_by_symbol: dict[str, list[dict[str, Any]]],
    symbols: list[str],
    benchmark_symbol: str,
    session_day: date,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build CLOSE features using only daily bars observable by that close.

    Unlike the intraday replay path, this helper can replay older history where
    5-minute Yahoo bars are unavailable. For a CLOSE checkpoint the full
    session OHLCV bar is observable at the close, so it is safe to append that
    day's daily bar and exclude every later session.
    """

    checkpoint = session_checkpoint(session_day, "CLOSE")
    benchmark_symbol = benchmark_symbol.upper()
    requested = sorted(set([*(symbol.upper() for symbol in symbols), benchmark_symbol]))

    benchmark_rows = daily_by_symbol.get(benchmark_symbol, [])
    benchmark_history = [
        dict(row)
        for row in benchmark_rows
        if _session_date(row.get("timestamp")) <= session_day
    ]
    benchmark_history.sort(key=lambda row: _parse_dt(row["timestamp"]))
    if len(benchmark_history) < 66:
        raise RuntimeError(f"insufficient historical benchmark history={len(benchmark_history)}")
    benchmark_close_by_day = {
        _session_date(row["timestamp"]).isoformat(): float(row["close"])
        for row in benchmark_history
    }

    latest_features: dict[str, dict[str, Any]] = {}
    source_sessions: dict[str, str] = {}
    for symbol in symbols:
        symbol = symbol.upper()
        rows = [
            dict(row)
            for row in daily_by_symbol.get(symbol, [])
            if _session_date(row.get("timestamp")) <= session_day
        ]
        rows.sort(key=lambda row: _parse_dt(row["timestamp"]))
        if len(rows) < 66:
            raise RuntimeError(f"insufficient historical history symbol={symbol} rows={len(rows)}")
        current = _daily_bar_for_session(rows, session_day)
        raw_rows: list[dict[str, Any]] = []
        for row in rows:
            row_day = _session_date(row["timestamp"])
            if row_day > session_day:
                raise RuntimeError(f"future daily row reached builder symbol={symbol} row={row_day}")
            day_key = row_day.isoformat()
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
        featured = build_features(raw_rows)
        if not featured:
            raise RuntimeError(f"feature builder returned no rows for {symbol}")
        latest = dict(featured[-1])
        if _session_date(latest.get("timestamp")) != session_day:
            raise RuntimeError(f"historical close feature date mismatch symbol={symbol}")
        latest_features[symbol] = latest
        source_sessions[symbol] = _session_date(current["timestamp"]).isoformat()

    if set(source_sessions.values()) != {session_day.isoformat()}:
        raise RuntimeError("historical close source sessions are not aligned")
    if not all(symbol in daily_by_symbol for symbol in requested):
        missing = sorted(set(requested) - set(daily_by_symbol))
        raise RuntimeError(f"missing requested daily series: {missing}")

    ordered = [latest_features[symbol.upper()] for symbol in symbols]
    return ordered, {
        "session": session_day.isoformat(),
        "event": "CLOSE",
        "checkpoint": checkpoint.isoformat(),
        "symbols": len(ordered),
        "benchmark": benchmark_symbol,
        "source": "completed_daily_bar_at_close",
        "source_session_by_symbol": source_sessions,
        "future_data_used": False,
    }
