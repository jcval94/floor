from __future__ import annotations

import argparse
import json
import sqlite3
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from floor.calendar import is_early_close, is_market_session, previous_market_session

ET = ZoneInfo("America/New_York")


def _parse_timestamp(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _latest_completed_session(now: datetime) -> date:
    now_et = now.astimezone(ET)
    today = now_et.date()
    if is_market_session(today):
        close_at = time(hour=13) if is_early_close(today) else time(hour=16)
        if now_et.time() >= (
            datetime.combine(today, close_at, tzinfo=ET) + timedelta(minutes=20)
        ).time():
            return today
    return previous_market_session(today)


def _missed_market_sessions(latest: date, required: date) -> int:
    if latest >= required:
        return 0
    count = 0
    cursor = latest + timedelta(days=1)
    while cursor <= required:
        if is_market_session(cursor):
            count += 1
        cursor += timedelta(days=1)
    return count


def validate_market_data_freshness(
    db_path: Path,
    symbols: Iterable[str],
    *,
    max_age_days: int = 7,
    max_stale_sessions: int | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    """Fail closed on missing/future/stale market data.

    ``max_stale_sessions`` is the canonical, market-calendar-aware mode and also
    rejects bars stamped on non-session dates. ``max_age_days`` remains for
    backwards-compatible historical/training checks where synthetic fixtures
    may use calendar dates.
    """
    requested = sorted(
        {str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()}
    )
    if not requested:
        raise RuntimeError("Market freshness validation refused: no symbols requested")
    if max_age_days < 0:
        raise ValueError("max_age_days must be >= 0")
    if max_stale_sessions is not None and max_stale_sessions < 0:
        raise ValueError("max_stale_sessions must be >= 0")
    if not db_path.exists():
        raise RuntimeError(f"Market freshness validation refused: DB missing at {db_path}")

    now = now or datetime.now(tz=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now_utc = now.astimezone(timezone.utc)
    now_et = now.astimezone(ET)
    required_session = _latest_completed_session(now_utc)

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
    non_session: list[dict[str, object]] = []

    for symbol in requested:
        raw = latest_by_symbol.get(symbol)
        if raw is None:
            continue
        try:
            timestamp = _parse_timestamp(raw)
        except ValueError:
            malformed.append({"symbol": symbol, "timestamp": raw})
            continue

        local_day = timestamp.astimezone(ET).date()
        if local_day > now_et.date():
            future.append({"symbol": symbol, "timestamp": raw})
            continue

        if max_stale_sessions is not None:
            if not is_market_session(local_day):
                non_session.append(
                    {"symbol": symbol, "timestamp": raw, "date": local_day.isoformat()}
                )
                continue
            missed = _missed_market_sessions(local_day, required_session)
            if missed > max_stale_sessions:
                stale.append(
                    {
                        "symbol": symbol,
                        "timestamp": raw,
                        "stale_sessions": missed,
                        "required_session": required_session.isoformat(),
                    }
                )
        else:
            age_days = (now_utc.date() - timestamp.date()).days
            if age_days < 0:
                future.append({"symbol": symbol, "timestamp": raw, "age_days": age_days})
            elif age_days > max_age_days:
                stale.append({"symbol": symbol, "timestamp": raw, "age_days": age_days})

    problems: list[str] = []
    if missing:
        problems.append(f"missing={','.join(missing)}")
    if malformed:
        problems.append("malformed=" + ",".join(str(item["symbol"]) for item in malformed))
    if future:
        problems.append("future=" + ",".join(str(item["symbol"]) for item in future))
    if non_session:
        problems.append("non_session=" + ",".join(str(item["symbol"]) for item in non_session))
    if stale:
        if max_stale_sessions is not None:
            problems.append(
                "stale="
                + ",".join(
                    f"{item['symbol']}:{item['stale_sessions']}sessions" for item in stale
                )
            )
        else:
            problems.append(
                "stale="
                + ",".join(f"{item['symbol']}:{item['age_days']}d" for item in stale)
            )

    summary: dict[str, object] = {
        "status": "OK" if not problems else "CRITICAL",
        "db_path": str(db_path),
        "symbols_requested": len(requested),
        "symbols_present": len(latest_by_symbol),
        "max_age_days": max_age_days,
        "max_stale_sessions": max_stale_sessions,
        "required_latest_session": required_session.isoformat(),
        "missing": missing,
        "malformed": malformed,
        "future": future,
        "non_session": non_session,
        "stale": stale,
    }
    if problems:
        raise RuntimeError(
            "Market freshness validation refused inference: " + "; ".join(problems)
        )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate market DB freshness before inference")
    parser.add_argument("--db", default="data/market/market_data.sqlite")
    parser.add_argument("--symbols", required=True, help="Comma-separated symbols")
    parser.add_argument("--max-age-days", type=int, default=7)
    parser.add_argument("--max-stale-sessions", type=int, default=None)
    args = parser.parse_args()
    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    try:
        summary = validate_market_data_freshness(
            Path(args.db),
            symbols,
            max_age_days=args.max_age_days,
            max_stale_sessions=args.max_stale_sessions,
        )
    except RuntimeError as exc:
        print(json.dumps({"status": "CRITICAL", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
