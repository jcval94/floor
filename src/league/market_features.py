from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from floor.persistence_db import latest_predictions
from storage.market_db import load_daily_bars


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _session_date(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return text[:10]


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _feature_row(symbol: str, bars: list[dict], spy_bars: list[dict]) -> dict[str, Any] | None:
    if len(bars) < 66 or len(spy_bars) < 21:
        return None
    latest = bars[-1]
    close = _safe_float(latest.get("close"))
    if close <= 0:
        return None

    closes = [_safe_float(row.get("close")) for row in bars]
    highs = [_safe_float(row.get("high")) for row in bars]
    lows = [_safe_float(row.get("low")) for row in bars]
    volumes = [_safe_float(row.get("volume")) for row in bars]
    spy_closes = [_safe_float(row.get("close")) for row in spy_bars]

    momentum_20 = close / max(closes[-21], 1e-9) - 1.0
    spy_momentum_20 = spy_closes[-1] / max(spy_closes[-21], 1e-9) - 1.0
    sma_65 = _mean(closes[-65:])
    peak_65 = max(closes[-65:])
    low_20 = min(lows[-20:])
    high_20 = max(highs[-20:])
    range_20 = high_20 - low_20

    true_ranges: list[float] = []
    for idx in range(max(1, len(bars) - 14), len(bars)):
        high = highs[idx]
        low = lows[idx]
        prev_close = closes[idx - 1]
        true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))

    return {
        "symbol": symbol,
        "timestamp": str(latest.get("timestamp") or ""),
        "open": _safe_float(latest.get("open")),
        "high": _safe_float(latest.get("high")),
        "low": _safe_float(latest.get("low")),
        "close": close,
        "volume": _safe_float(latest.get("volume")),
        "momentum_20": momentum_20,
        "rel_strength_20": momentum_20 - spy_momentum_20,
        "trend_context_m3": close / max(sma_65, 1e-9) - 1.0,
        "drawdown_13w": close / max(peak_65, 1e-9) - 1.0,
        "atr_14": _mean(true_ranges),
        "price_position_in_range_20": (
            (close - low_20) / range_20 if range_20 > 1e-9 else 0.5
        ),
        "avg_dollar_volume": _mean(
            [c * v for c, v in zip(closes[-20:], volumes[-20:])]
        ),
    }


def build_league_market_snapshot(
    market_db: Path,
    persistence_db: Path,
    symbols: list[str],
    *,
    benchmark_symbol: str = "SPY",
) -> dict[str, Any]:
    """Build the current league input with one local SQLite scan and no network calls."""
    benchmark_symbol = benchmark_symbol.upper()
    requested = sorted(set([*(symbol.upper() for symbol in symbols), benchmark_symbol]))
    bars = load_daily_bars(market_db, requested)
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for row in bars:
        by_symbol[str(row.get("symbol") or "").upper()].append(row)
    for values in by_symbol.values():
        values.sort(key=lambda row: str(row.get("timestamp") or ""))

    if not by_symbol.get(benchmark_symbol):
        return {"status": "WAITING_FOR_MARKET_DATA", "session": None, "rows": [], "bars": {}}

    benchmark_bars = by_symbol[benchmark_symbol]
    session = _session_date(benchmark_bars[-1].get("timestamp"))
    predictions = latest_predictions(persistence_db)
    pred_by_key = {
        (str(row.get("symbol") or "").upper(), str(row.get("horizon") or "").lower()): row
        for row in predictions
    }

    enriched: list[dict[str, Any]] = []
    complete_symbols: list[str] = []
    for symbol in symbols:
        symbol = symbol.upper()
        feature = _feature_row(symbol, by_symbol.get(symbol, []), benchmark_bars)
        if feature is None or _session_date(feature.get("timestamp")) != session:
            continue
        horizons = {h: pred_by_key.get((symbol, h)) for h in ("d1", "w1", "q1", "m3")}
        if any(value is None for value in horizons.values()):
            continue
        if any(_session_date(value.get("as_of")) != session for value in horizons.values() if value):
            continue

        row = dict(feature)
        for horizon in ("d1", "w1", "q1"):
            pred = horizons[horizon] or {}
            row[f"floor_{horizon}"] = pred.get("floor_value")
            row[f"ceiling_{horizon}"] = pred.get("ceiling_value")
            row[f"floor_time_bucket_{horizon}"] = pred.get("floor_time_bucket")
            row[f"ceiling_time_bucket_{horizon}"] = pred.get("ceiling_time_bucket")
            if horizon == "d1":
                row["confidence_score"] = pred.get("confidence_score")

        m3 = horizons["m3"] or {}
        for field in (
            "floor_m3",
            "floor_week_m3",
            "floor_week_m3_confidence",
            "floor_week_m3_top3",
            "m3_status",
            "m3_block_reason",
        ):
            row[field] = m3.get(field)

        floor_d1 = _safe_float(row.get("floor_d1"))
        ceiling_d1 = _safe_float(row.get("ceiling_d1"))
        if floor_d1 > 0 and ceiling_d1 > floor_d1:
            row["expected_range_d1"] = ceiling_d1 - floor_d1
            downside = max(row["close"] - floor_d1, 1e-9)
            row["reward_risk_ratio"] = max(0.0, ceiling_d1 - row["close"]) / downside
        else:
            row["expected_range_d1"] = 0.0
            row["reward_risk_ratio"] = 0.0
        row["expected_return_d1"] = None
        row["expected_return_w1"] = None
        row["expected_return_q1"] = None
        row["expected_return_m3"] = None
        enriched.append(row)
        complete_symbols.append(symbol)

    latest_bars = {
        symbol: values[-1]
        for symbol, values in by_symbol.items()
        if values and _session_date(values[-1].get("timestamp")) == session
    }
    status = "OK" if len(complete_symbols) == len(symbols) else "INCOMPLETE_FORECAST_INPUT"
    return {
        "status": status,
        "session": session,
        "rows": enriched,
        "bars": latest_bars,
        "complete_symbols": sorted(complete_symbols),
        "expected_symbols": sorted(symbol.upper() for symbol in symbols),
    }
