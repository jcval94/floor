from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


def _parse_timestamp(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def validate_market_data_freshness(
    db_path: Path,
    symbols: Iterable[str],
    *,
    max_age_days: int = 7,
    now: datetime | None = None,
) -> dict[str, object]:
    """Fail closed when market data is missing, malformed, stale, or future-dated."""

    requested = sorted({str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()})
    if not requested:
        raise RuntimeError("Market freshness validation refused: no symbols requested")
    if max_age_days < 0:
        raise ValueError("max_age_days must be >= 0")
    if not db_path.exists():
        raise RuntimeError(f"Market freshness validation refused: DB missing at {db_path}")

    now = now or datetime.now(tz=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now_utc = now.astimezone(timezone.utc)

    placeholders = ",".join("?" for _ in requested)
    query = f"""
        SELECT symbol, MAX(ts_utc)
        FROM daily_bars
        WHERE symbol IN ({placeholders})
        GROUP BY symbol
    """
    try:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(query, requested).fetchall()
    except sqlite3.Error as exc:
        raise RuntimeError(f"Market freshness validation failed reading {db_path}: {exc}") from exc

    latest_by_symbol = {str(symbol).upper(): str(ts) for symbol, ts in rows if ts}
    missing = [symbol for symbol in requested if symbol not in latest_by_symbol]
    stale: list[dict[str, object]] = []
    future: list[dict[str, object]] = []
    malformed: list[dict[str, object]] = []

    for symbol in requested:
        raw = latest_by_symbol.get(symbol)
        if raw is None:
            continue
        try:
            timestamp = _parse_timestamp(raw)
        except ValueError:
            malformed.append({"symbol": symbol, "timestamp": raw})
            continue

        age_days = (now_utc.date() - timestamp.date()).days
        if age_days < 0:
            future.append({"symbol": symbol, "timestamp": raw, "age_days": age_days})
        elif age_days > max_age_days:
            stale.append({"symbol": symbol, "timestamp": raw, "age_days": age_days})

    problems: list[str] = []
    if missing:
        problems.append(f"missing={','.join(missing)}")
    if malformed:
        problems.append("malformed=" + ",".join(item["symbol"] for item in malformed))
    if future:
        problems.append("future=" + ",".join(item["symbol"] for item in future))
    if stale:
        problems.append(
            "stale=" + ",".join(f"{item['symbol']}:{item['age_days']}d" for item in stale)
        )

    summary: dict[str, object] = {
        "status": "OK" if not problems else "CRITICAL",
        "db_path": str(db_path),
        "symbols_requested": len(requested),
        "symbols_present": len(latest_by_symbol),
        "max_age_days": max_age_days,
        "missing": missing,
        "malformed": malformed,
        "future": future,
        "stale": stale,
    }
    if problems:
        raise RuntimeError("Market freshness validation refused inference: " + "; ".join(problems))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate market DB freshness before inference")
    parser.add_argument("--db", default="data/market/market_data.sqlite")
    parser.add_argument("--symbols", required=True, help="Comma-separated symbols")
    parser.add_argument("--max-age-days", type=int, default=7)
    args = parser.parse_args()

    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    try:
        summary = validate_market_data_freshness(
            Path(args.db),
            symbols,
            max_age_days=args.max_age_days,
        )
    except RuntimeError as exc:
        print(json.dumps({"status": "CRITICAL", "error": str(exc)}, ensure_ascii=False))
        return 1

    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
