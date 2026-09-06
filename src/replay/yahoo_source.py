from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

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
