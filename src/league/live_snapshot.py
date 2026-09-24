from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from league.engine import build_leaderboard, load_state
from storage.yahoo_ingest import fetch_yahoo_chart, parse_daily_bars


ET = ZoneInfo("America/New_York")
BENCHMARK_IDS = {"benchmark_spy", "benchmark_equal_weight"}


def _load_object(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_object(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _quote_session(as_of: Any) -> str:
    text = str(as_of or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text[:10]
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(ET).date().isoformat()


def _member_types(league_cfg: dict[str, Any]) -> dict[str, str]:
    raw = league_cfg.get("members", [])
    if not isinstance(raw, list):
        return {}
    return {
        str(item.get("id")): str(item.get("type") or "strategy")
        for item in raw
        if isinstance(item, dict) and item.get("id")
    }


def _waiting_base(league_cfg: dict[str, Any], detail: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "league_id": str(league_cfg.get("league_id") or ""),
        "mode": "shadow_paper_mark_to_market_base",
        "status": "WAITING_FOR_GENESIS",
        "detail": detail,
        "last_eod_session": None,
        "sessions": 0,
        "initial_nav_usd": _number(league_cfg.get("initial_nav_usd"), 10000.0),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "members": {},
        "automatic_promotion": False,
        "live_execution_enabled": False,
    }


def export_live_base(
    data_dir: Path,
    league_config_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    """Export the minimum official EOD state needed for intraday mark-to-market.

    The export excludes pending targets and audit history. Intraday monitoring may
    mark existing positions but cannot mutate official Strategy League state.
    """

    league_cfg = _load_object(league_config_path)
    league_id = str(league_cfg.get("league_id") or "")
    if not league_id:
        raise RuntimeError("strategy_league.json must define league_id")

    state_dir = data_dir / "metrics" / "strategy_league" / "runs" / league_id
    state = load_state(state_dir)
    if state is None:
        payload = _waiting_base(
            league_cfg,
            f"No official Strategy League state exists yet for {league_id}.",
        )
        _write_object(output_path, payload)
        return payload

    leaderboard = build_leaderboard(state, league_cfg)
    official_rows = {
        str(row.get("strategy")): row
        for row in leaderboard.get("rows", [])
        if isinstance(row, dict)
    }
    configured_types = _member_types(league_cfg)
    members: dict[str, Any] = {}

    for member_id, member_raw in state.get("members", {}).items():
        if not isinstance(member_raw, dict):
            continue
        official = official_rows.get(str(member_id), {})
        positions: dict[str, Any] = {}
        for symbol, position_raw in member_raw.get("positions", {}).items():
            if not isinstance(position_raw, dict):
                continue
            qty = int(position_raw.get("qty", 0) or 0)
            if qty <= 0:
                continue
            positions[str(symbol).upper()] = {
                "qty": qty,
                "cost_basis": _number(position_raw.get("cost_basis")),
                "last_price": _number(position_raw.get("last_price")),
            }

        members[str(member_id)] = {
            "member_type": configured_types.get(
                str(member_id),
                "benchmark" if str(member_id) in BENCHMARK_IDS else "strategy",
            ),
            "cash": _number(member_raw.get("cash")),
            "positions": positions,
            "eod_nav": _number(
                official.get("nav"),
                _number(league_cfg.get("initial_nav_usd"), 10000.0),
            ),
            "official_return": official.get("return"),
            "official_sharpe": official.get("sharpe"),
            "official_max_drawdown": official.get("max_drawdown"),
            "trades": int(
                official.get("trades", member_raw.get("trade_count", 0)) or 0
            ),
            "costs_paid": _number(
                official.get("costs_paid"),
                _number(member_raw.get("costs_paid")),
            ),
        }

    payload = {
        "schema_version": 1,
        "league_id": league_id,
        "mode": "shadow_paper_mark_to_market_base",
        "status": "READY",
        "last_eod_session": state.get("last_session"),
        "sessions": int(state.get("session_count", 0) or 0),
        "initial_nav_usd": _number(league_cfg.get("initial_nav_usd"), 10000.0),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "members": members,
        "automatic_promotion": False,
        "live_execution_enabled": False,
    }
    _write_object(output_path, payload)
    return payload


def _latest_quote(symbol: str, range_: str, interval: str) -> dict[str, Any] | None:
    yahoo_symbol = symbol.upper().replace(".", "-")
    payload = fetch_yahoo_chart(yahoo_symbol, range_, interval)
    bars = parse_daily_bars(symbol, payload)
    if not bars:
        return None
    latest = bars[-1]
    if latest.close <= 0:
        return None
    return {
        "symbol": symbol.upper(),
        "price": float(latest.close),
        "as_of": latest.ts_utc,
        "source": "yahoo_chart",
        "interval": interval,
    }


def fetch_live_quotes(
    symbols: list[str],
    *,
    range_: str = "1d",
    interval: str = "5m",
    max_workers: int = 8,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Fetch Yahoo intraday bars without writing them into the daily-bar database."""

    normalized = sorted({symbol.upper() for symbol in symbols if symbol})
    if not normalized:
        return {}, []

    quotes: dict[str, dict[str, Any]] = {}
    failed: list[str] = []
    workers = max(1, min(max_workers, len(normalized)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_latest_quote, symbol, range_, interval): symbol
            for symbol in normalized
        }
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                quote = future.result()
            except Exception:
                quote = None
            if quote is None:
                failed.append(symbol)
            else:
                quotes[symbol] = quote
    return quotes, sorted(failed)


def _curve_from_previous(
    previous_snapshot: dict[str, Any],
    strategy: str,
    market_session: str,
) -> list[dict[str, Any]]:
    if str(previous_snapshot.get("market_session") or "") != market_session:
        return []
    rows = previous_snapshot.get("rows", [])
    if not isinstance(rows, list):
        return []
    for row in rows:
        if isinstance(row, dict) and str(row.get("strategy")) == strategy:
            curve = row.get("intraday_curve", [])
            if isinstance(curve, list):
                return [
                    dict(point) for point in curve if isinstance(point, dict)
                ][-39:]
    return []


def _effective_quote(
    symbol: str,
    position: dict[str, Any],
    current_quotes: dict[str, dict[str, Any]],
    previous_cache: dict[str, dict[str, Any]],
    market_session: str,
) -> tuple[float, str]:
    current = current_quotes.get(symbol, {})
    if _quote_session(current.get("as_of")) == market_session:
        price = _number(current.get("price"))
        if price > 0:
            return price, "fresh"

    previous = previous_cache.get(symbol, {})
    if _quote_session(previous.get("as_of")) == market_session:
        price = _number(previous.get("price"))
        if price > 0:
            return price, "cached"

    return _number(position.get("last_price")), "eod_fallback"


def _rank_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows.sort(
        key=lambda row: (
            _number(row.get("return"), float("-inf")),
            str(row.get("strategy") or ""),
        ),
        reverse=True,
    )
    previous_return: float | None = None
    previous_rank = 0
    for index, row in enumerate(rows, start=1):
        current_return = _number(row.get("return"), float("-inf"))
        if (
            index == 1
            or previous_return is None
            or abs(current_return - previous_return) > 1e-12
        ):
            previous_rank = index
        row["rank"] = previous_rank
        previous_return = current_return
    return rows


def build_live_snapshot(
    base: dict[str, Any],
    quotes: dict[str, dict[str, Any]],
    *,
    previous_snapshot: dict[str, Any] | None = None,
    now: datetime | None = None,
    failed_symbols: list[str] | None = None,
) -> dict[str, Any]:
    """Mark the official EOD portfolio state with intraday prices, without trading."""

    previous_snapshot = previous_snapshot or {}
    failed_symbols = failed_symbols or []
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now_et = now.astimezone(ET)
    market_session = now_et.date().isoformat()
    generated_at = now.astimezone(timezone.utc).isoformat()
    timestamp_label = now_et.strftime("%H:%M")

    if str(base.get("status") or "") != "READY":
        return {
            "schema_version": 1,
            "league_id": base.get("league_id"),
            "mode": "shadow_paper_mark_to_market",
            "status": "WAITING_FOR_GENESIS",
            "detail": base.get("detail")
            or "Official EOD Strategy League base is unavailable.",
            "market_session": market_session,
            "generated_at": generated_at,
            "last_eod_session": base.get("last_eod_session"),
            "sessions": int(base.get("sessions", 0) or 0),
            "initial_nav_usd": _number(base.get("initial_nav_usd"), 10000.0),
            "evidence_type": "intraday_mark_to_market_non_promotional",
            "counts_as_prospective_evidence": False,
            "automatic_promotion": False,
            "live_execution_enabled": False,
            "quote_source": {
                "provider": "Yahoo Finance chart",
                "interval": "5m",
                "required_symbols": 0,
                "fresh_symbols": 0,
                "fresh_coverage": 1.0,
            },
            "rows": [],
            "quote_cache": {},
        }

    previous_cache_raw = previous_snapshot.get("quote_cache", {})
    previous_cache = (
        previous_cache_raw if isinstance(previous_cache_raw, dict) else {}
    )
    current_quotes = {
        symbol.upper(): dict(quote)
        for symbol, quote in quotes.items()
        if isinstance(quote, dict)
        and _quote_session(quote.get("as_of")) == market_session
        and _number(quote.get("price")) > 0
    }

    members_raw = base.get("members", {})
    members = members_raw if isinstance(members_raw, dict) else {}
    required_symbols = sorted(
        {
            str(symbol).upper()
            for member in members.values()
            if isinstance(member, dict)
            for symbol, position in member.get("positions", {}).items()
            if isinstance(position, dict) and int(position.get("qty", 0) or 0) > 0
        }
    )
    fresh_symbols = sorted(set(required_symbols) & set(current_quotes))
    fresh_coverage = (
        len(fresh_symbols) / len(required_symbols) if required_symbols else 1.0
    )

    quote_cache: dict[str, dict[str, Any]] = {}
    for symbol in required_symbols:
        current = current_quotes.get(symbol)
        if current:
            quote_cache[symbol] = {
                "price": _number(current.get("price")),
                "as_of": current.get("as_of"),
            }
            continue
        previous = previous_cache.get(symbol)
        if (
            isinstance(previous, dict)
            and _quote_session(previous.get("as_of")) == market_session
        ):
            quote_cache[symbol] = {
                "price": _number(previous.get("price")),
                "as_of": previous.get("as_of"),
            }

    initial_nav = _number(base.get("initial_nav_usd"), 10000.0)
    rows: list[dict[str, Any]] = []
    for member_id, member_raw in members.items():
        if not isinstance(member_raw, dict):
            continue
        positions_raw = member_raw.get("positions", {})
        positions = positions_raw if isinstance(positions_raw, dict) else {}
        cash = _number(member_raw.get("cash"))
        market_value = 0.0
        unrealized_pnl = 0.0
        fresh_count = 0
        cached_count = 0
        fallback_count = 0
        position_count = 0

        for symbol_raw, position_raw in positions.items():
            if not isinstance(position_raw, dict):
                continue
            symbol = str(symbol_raw).upper()
            qty = int(position_raw.get("qty", 0) or 0)
            if qty <= 0:
                continue
            position_count += 1
            price, source = _effective_quote(
                symbol,
                position_raw,
                current_quotes,
                previous_cache,
                market_session,
            )
            if source == "fresh":
                fresh_count += 1
            elif source == "cached":
                cached_count += 1
            else:
                fallback_count += 1
            market_value += qty * price
            unrealized_pnl += qty * (
                price - _number(position_raw.get("cost_basis"), price)
            )

        nav = cash + market_value
        eod_nav = _number(member_raw.get("eod_nav"), initial_nav)
        cumulative_return = (
            nav / initial_nav - 1.0 if initial_nav > 0 else 0.0
        )
        change_since_eod = nav / eod_nav - 1.0 if eod_nav > 0 else 0.0
        coverage = fresh_count / position_count if position_count else 1.0
        curve = _curve_from_previous(
            previous_snapshot,
            str(member_id),
            market_session,
        )
        point = {"session": timestamp_label, "nav": round(nav, 6)}
        if not curve or str(curve[-1].get("session")) != timestamp_label:
            curve.append(point)
        else:
            curve[-1] = point

        rows.append(
            {
                "strategy": str(member_id),
                "member_type": member_raw.get(
                    "member_type",
                    "benchmark"
                    if str(member_id) in BENCHMARK_IDS
                    else "strategy",
                ),
                "nav": round(nav, 6),
                "eod_nav": round(eod_nav, 6),
                "return": cumulative_return,
                "change_since_eod": change_since_eod,
                "vs_spy": None,
                "gross_exposure_pct": market_value / nav if nav > 0 else 0.0,
                "cash_pct": cash / nav if nav > 0 else 0.0,
                "position_count": position_count,
                "unrealized_pnl": round(unrealized_pnl, 6),
                "quote_coverage": coverage,
                "fresh_quotes": fresh_count,
                "cached_quotes": cached_count,
                "fallback_quotes": fallback_count,
                "official_sharpe": member_raw.get("official_sharpe"),
                "official_max_drawdown": member_raw.get(
                    "official_max_drawdown"
                ),
                "trades": int(member_raw.get("trades", 0) or 0),
                "costs_paid": _number(member_raw.get("costs_paid")),
                "last_eod_session": base.get("last_eod_session"),
                "intraday_curve": curve[-40:],
            }
        )

    spy = next(
        (row for row in rows if row.get("strategy") == "benchmark_spy"),
        None,
    )
    spy_return = _number(spy.get("return")) if spy else None
    for row in rows:
        row_return = _number(row.get("return"))
        row["vs_spy"] = (
            row_return - spy_return if spy_return is not None else None
        )

    rows = _rank_rows(rows)
    strategies = [
        row for row in rows if str(row.get("member_type")) != "benchmark"
    ]
    challenger = next(
        (
            row
            for row in rows
            if row.get("strategy") == "capital_allocation_challenger"
        ),
        None,
    )
    freshest = max(
        (
            str(quote.get("as_of") or "")
            for quote in current_quotes.values()
            if quote.get("as_of")
        ),
        default=None,
    )
    status = "LIVE" if fresh_coverage >= 0.95 else "DEGRADED"

    return {
        "schema_version": 1,
        "league_id": base.get("league_id"),
        "mode": "shadow_paper_mark_to_market",
        "status": status,
        "detail": (
            "Fresh intraday quotes cover the marked portfolio universe."
            if status == "LIVE"
            else "Some positions use a same-session cache or the last official EOD price."
        ),
        "market_session": market_session,
        "generated_at": generated_at,
        "last_eod_session": base.get("last_eod_session"),
        "sessions": int(base.get("sessions", 0) or 0),
        "initial_nav_usd": initial_nav,
        "evidence_type": "intraday_mark_to_market_non_promotional",
        "counts_as_prospective_evidence": False,
        "automatic_promotion": False,
        "live_execution_enabled": False,
        "quote_source": {
            "provider": "Yahoo Finance chart",
            "interval": "5m",
            "required_symbols": len(required_symbols),
            "fresh_symbols": len(fresh_symbols),
            "fresh_coverage": fresh_coverage,
            "freshest_quote_at": freshest,
            "failed_symbols": sorted(set(failed_symbols)),
        },
        "summary": {
            "live_strategy_leader": strategies[0].get("strategy")
            if strategies
            else None,
            "live_strategy_leader_return": strategies[0].get("return")
            if strategies
            else None,
            "challenger_rank": challenger.get("rank")
            if challenger
            else None,
            "challenger_return": challenger.get("return")
            if challenger
            else None,
            "challenger_change_since_eod": challenger.get(
                "change_since_eod"
            )
            if challenger
            else None,
            "challenger_vs_spy": challenger.get("vs_spy")
            if challenger
            else None,
        },
        "rows": rows,
        "quote_cache": quote_cache,
    }


def mark_live_snapshot(
    base_path: Path,
    output_path: Path,
    *,
    previous_snapshot_path: Path | None = None,
    range_: str = "1d",
    interval: str = "5m",
) -> dict[str, Any]:
    base = _load_object(base_path)
    previous = _load_object(previous_snapshot_path)
    symbols = sorted(
        {
            str(symbol).upper()
            for member in base.get("members", {}).values()
            if isinstance(member, dict)
            for symbol, position in member.get("positions", {}).items()
            if isinstance(position, dict)
            and int(position.get("qty", 0) or 0) > 0
        }
    )
    quotes, failed = fetch_live_quotes(
        symbols,
        range_=range_,
        interval=interval,
    )
    payload = build_live_snapshot(
        base,
        quotes,
        previous_snapshot=previous,
        failed_symbols=failed,
    )
    _write_object(output_path, payload)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build observational intraday Strategy League mark-to-market snapshots"
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser(
        "export-base",
        help="Export compact official EOD state for intraday marking",
    )
    export_parser.add_argument("--data-dir", default="data")
    export_parser.add_argument(
        "--league-config",
        default="config/strategy_league.json",
    )
    export_parser.add_argument(
        "--output",
        default="data/metrics/strategy_league/live_base.json",
    )

    mark_parser = subparsers.add_parser(
        "mark",
        help="Fetch intraday quotes and mark the compact EOD state",
    )
    mark_parser.add_argument(
        "--base",
        default="data/metrics/strategy_league/live_base.json",
    )
    mark_parser.add_argument(
        "--previous-snapshot",
        default="data/metrics/strategy_league/live_snapshot.json",
    )
    mark_parser.add_argument(
        "--output",
        default="data/metrics/strategy_league/live_snapshot.json",
    )
    mark_parser.add_argument("--range", default="1d")
    mark_parser.add_argument("--interval", default="5m")

    args = parser.parse_args()
    if args.command == "export-base":
        payload = export_live_base(
            Path(args.data_dir),
            Path(args.league_config),
            Path(args.output),
        )
    else:
        payload = mark_live_snapshot(
            Path(args.base),
            Path(args.output),
            previous_snapshot_path=Path(args.previous_snapshot),
            range_=str(args.range),
            interval=str(args.interval),
        )

    print(
        json.dumps(
            {
                "status": payload.get("status"),
                "league_id": payload.get("league_id"),
                "generated_at": payload.get("generated_at"),
                "rows": len(payload.get("rows", [])),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
