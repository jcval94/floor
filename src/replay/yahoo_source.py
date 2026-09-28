from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timezone
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


def _symbol_to_yahoo(symbol: str) -> str:
    return symbol.upper().replace(".", "-")


def _to_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float, str)):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    return None


def fetch_chart(symbol: str, *, range_: str, interval: str) -> dict[str, Any]:
    yahoo_symbol = _symbol_to_yahoo(symbol)
    base = f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_symbol}"
    query = urlencode(
        {
            "range": range_,
            "interval": interval,
            "events": "div,splits",
            "includePrePost": "false",
        }
    )
    request = Request(
        f"{base}?{query}",
        headers={"User-Agent": "floor-replay/1.0 (research; point-in-time audit)"},
    )
    with urlopen(request, timeout=30) as response:  # noqa: S310
        payload = json.loads(response.read().decode("utf-8"))
    error = payload.get("chart", {}).get("error")
    if error:
        raise RuntimeError(f"Yahoo chart error symbol={symbol}: {error}")
    return payload


def parse_chart_rows(symbol: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    result = (payload.get("chart", {}).get("result") or [{}])[0]
    timestamps = result.get("timestamp") or []
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    volumes = quote.get("volume") or []

    rows: list[dict[str, Any]] = []
    for idx, raw_ts in enumerate(timestamps):
        values = [
            opens[idx] if idx < len(opens) else None,
            highs[idx] if idx < len(highs) else None,
            lows[idx] if idx < len(lows) else None,
            closes[idx] if idx < len(closes) else None,
            volumes[idx] if idx < len(volumes) else None,
        ]
        converted = [_to_float(value) for value in values]
        if any(value is None for value in converted):
            continue
        open_, high, low, close, volume = converted
        assert open_ is not None
        assert high is not None
        assert low is not None
        assert close is not None
        assert volume is not None
        rows.append(
            {
                "symbol": symbol.upper(),
                "timestamp": datetime.fromtimestamp(
                    int(raw_ts), tz=timezone.utc
                ).isoformat(),
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            }
        )
    return rows


def fetch_rows_with_retries(
    symbol: str,
    *,
    range_: str,
    interval: str,
    attempts: int = 3,
) -> list[dict[str, Any]]:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            payload = fetch_chart(symbol, range_=range_, interval=interval)
            rows = parse_chart_rows(symbol, payload)
            if not rows:
                raise RuntimeError(
                    f"Yahoo returned no usable rows symbol={symbol} "
                    f"range={range_} interval={interval}"
                )
            return rows
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
            last_error = exc
            logger.warning(
                "Yahoo replay fetch failed symbol=%s interval=%s attempt=%s error=%s",
                symbol,
                interval,
                attempt,
                exc,
            )
            if attempt < attempts:
                time.sleep(float(attempt))
    assert last_error is not None
    raise RuntimeError(
        f"Yahoo replay fetch exhausted retries symbol={symbol} interval={interval}"
    ) from last_error




ET = ZoneInfo("America/New_York")


def _row_session_day(row: dict[str, Any]) -> date:
    return datetime.fromisoformat(
        str(row["timestamp"]).replace("Z", "+00:00")
    ).astimezone(ET).date()


def _missing_daily_sessions_by_symbol(
    rows: list[dict[str, Any]],
    *,
    symbols: Iterable[str],
    required_sessions: Iterable[date],
    benchmark_symbol: str = "SPY",
) -> dict[str, list[date]]:
    required = list(required_sessions)
    required_set = set(required)
    requested = sorted(
        set([*(str(symbol).upper() for symbol in symbols), benchmark_symbol.upper()])
    )
    present: dict[str, set[date]] = {symbol: set() for symbol in requested}
    for row in rows:
        symbol = str(row.get("symbol") or "").upper()
        if symbol not in present:
            continue
        session_day = _row_session_day(row)
        if session_day in required_set:
            present[symbol].add(session_day)
    return {
        symbol: [session_day for session_day in required if session_day not in present[symbol]]
        for symbol in requested
        if any(session_day not in present[symbol] for session_day in required)
    }


def _merge_missing_daily_rows(
    base_rows: list[dict[str, Any]],
    repair_rows: list[dict[str, Any]],
    *,
    allowed_missing: dict[str, list[date]],
) -> tuple[list[dict[str, Any]], int]:
    """Append only truly missing symbol/session bars; never replace known history."""

    missing_keys = {
        (symbol.upper(), session_day)
        for symbol, sessions in allowed_missing.items()
        for session_day in sessions
    }
    existing = {
        (str(row.get("symbol") or "").upper(), _row_session_day(row))
        for row in base_rows
    }
    merged = list(base_rows)
    added = 0
    for row in repair_rows:
        key = (str(row.get("symbol") or "").upper(), _row_session_day(row))
        if key not in missing_keys or key in existing:
            continue
        merged.append(dict(row))
        existing.add(key)
        added += 1
    return merged, added


def repair_replay_daily_market_data(
    daily_rows: list[dict[str, Any]],
    *,
    symbols: list[str],
    required_sessions: list[date],
    benchmark_symbol: str = "SPY",
    sleep_seconds: float = 0.15,
    repair_ranges: tuple[str, ...] = ("3mo", "6mo", "2y"),
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Repair transient Yahoo daily gaps before conservative session trimming.

    The initial 2y download remains authoritative. Repair queries may only add
    bars for symbol/session pairs that are absent from that initial snapshot;
    they never overwrite an already observed daily bar.
    """

    initial_missing = _missing_daily_sessions_by_symbol(
        daily_rows,
        symbols=symbols,
        required_sessions=required_sessions,
        benchmark_symbol=benchmark_symbol,
    )
    if not initial_missing:
        return daily_rows, {
            "attempted": False,
            "initial_missing_sessions_by_symbol": {},
            "recovered_sessions_by_symbol": {},
            "unresolved_missing_sessions_by_symbol": {},
            "repair_attempts": [],
            "added_rows": 0,
        }

    repaired = list(daily_rows)
    attempts: list[dict[str, Any]] = []
    total_added = 0

    for range_ in repair_ranges:
        current_missing = _missing_daily_sessions_by_symbol(
            repaired,
            symbols=symbols,
            required_sessions=required_sessions,
            benchmark_symbol=benchmark_symbol,
        )
        if not current_missing:
            break

        added_this_pass = 0
        failures: list[dict[str, str]] = []
        for symbol in sorted(current_missing):
            try:
                fetched = fetch_rows_with_retries(
                    symbol,
                    range_=range_,
                    interval="1d",
                    attempts=4,
                )
                repaired, added = _merge_missing_daily_rows(
                    repaired,
                    fetched,
                    allowed_missing=current_missing,
                )
                added_this_pass += added
            except Exception as exc:
                failures.append({"symbol": symbol, "error": str(exc)})
            time.sleep(sleep_seconds)

        total_added += added_this_pass
        remaining = _missing_daily_sessions_by_symbol(
            repaired,
            symbols=symbols,
            required_sessions=required_sessions,
            benchmark_symbol=benchmark_symbol,
        )
        attempts.append(
            {
                "range": range_,
                "symbols_requested": len(current_missing),
                "added_rows": added_this_pass,
                "remaining_missing_symbols": len(remaining),
                "failures": failures,
            }
        )

    unresolved = _missing_daily_sessions_by_symbol(
        repaired,
        symbols=symbols,
        required_sessions=required_sessions,
        benchmark_symbol=benchmark_symbol,
    )
    recovered: dict[str, list[str]] = {}
    for symbol, sessions in initial_missing.items():
        unresolved_set = set(unresolved.get(symbol, []))
        restored = [session.isoformat() for session in sessions if session not in unresolved_set]
        if restored:
            recovered[symbol] = restored

    return repaired, {
        "attempted": True,
        "initial_missing_sessions_by_symbol": {
            symbol: [session.isoformat() for session in sessions]
            for symbol, sessions in initial_missing.items()
        },
        "recovered_sessions_by_symbol": recovered,
        "unresolved_missing_sessions_by_symbol": {
            symbol: [session.isoformat() for session in sessions]
            for symbol, sessions in unresolved.items()
        },
        "repair_attempts": attempts,
        "added_rows": total_added,
    }


def fetch_replay_daily_market_data(
    symbols: list[str],
    *,
    benchmark_symbol: str = "SPY",
    sleep_seconds: float = 0.15,
    required_sessions: list[date] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fetch durable daily history for historical CLOSE-only replays.

    Daily bars remain available well beyond Yahoo's short intraday retention
    window and are sufficient at a CLOSE checkpoint because the completed
    session OHLCV bar is observable at that time.
    """

    requested = sorted(
        set([*(symbol.upper() for symbol in symbols), benchmark_symbol.upper()])
    )
    daily: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []

    for symbol in requested:
        try:
            daily.extend(
                fetch_rows_with_retries(symbol, range_="2y", interval="1d")
            )
        except Exception as exc:
            failures.append({"symbol": symbol, "error": str(exc)})
        time.sleep(sleep_seconds)

    if failures:
        raise RuntimeError(f"incomplete replay daily market download: {failures}")

    repair_audit = {
        "attempted": False,
        "initial_missing_sessions_by_symbol": {},
        "recovered_sessions_by_symbol": {},
        "unresolved_missing_sessions_by_symbol": {},
        "repair_attempts": [],
        "added_rows": 0,
    }
    if required_sessions:
        daily, repair_audit = repair_replay_daily_market_data(
            daily,
            symbols=symbols,
            required_sessions=required_sessions,
            benchmark_symbol=benchmark_symbol,
            sleep_seconds=sleep_seconds,
        )

    summary = {
        "symbols": len(requested),
        "daily_rows": len(daily),
        "intraday_rows": 0,
        "daily_source": "Yahoo chart range=2y interval=1d + targeted missing-session repair",
        "intraday_source": None,
        "checkpoint_mode": "completed_daily_bar_at_close",
        "failures": failures,
        "daily_gap_repair": repair_audit,
    }
    return daily, summary

def fetch_replay_market_data(
    symbols: list[str],
    *,
    benchmark_symbol: str = "SPY",
    sleep_seconds: float = 0.15,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    requested = sorted(set([*(symbol.upper() for symbol in symbols), benchmark_symbol.upper()]))
    daily: list[dict[str, Any]] = []
    intraday: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []

    for symbol in requested:
        try:
            daily.extend(
                fetch_rows_with_retries(symbol, range_="2y", interval="1d")
            )
            intraday.extend(
                fetch_rows_with_retries(symbol, range_="1mo", interval="5m")
            )
        except Exception as exc:
            failures.append({"symbol": symbol, "error": str(exc)})
        time.sleep(sleep_seconds)

    if failures:
        raise RuntimeError(f"incomplete replay market download: {failures}")

    summary = {
        "symbols": len(requested),
        "daily_rows": len(daily),
        "intraday_rows": len(intraday),
        "daily_source": "Yahoo chart range=2y interval=1d",
        "intraday_source": "Yahoo chart range=1mo interval=5m",
        "failures": failures,
    }
    return daily, intraday, summary
