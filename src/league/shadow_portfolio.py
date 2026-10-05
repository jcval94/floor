from __future__ import annotations

import argparse
import copy
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from floor.calendar import previous_market_session
from league.engine import _execute_target, _portfolio_nav, _trade
from storage.yahoo_ingest import fetch_yahoo_chart, parse_daily_bars

ET = ZoneInfo("America/New_York")


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _as_dt(value: Any) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _fetch_symbol(symbol: str, *, now: datetime, range_: str, interval: str) -> tuple[str, list[dict[str, float | str]]]:
    payload = fetch_yahoo_chart(symbol.upper().replace(".", "-"), range_, interval)
    parsed = parse_daily_bars(symbol, payload)
    now_utc = now.astimezone(timezone.utc)
    session = now.astimezone(ET).date()
    minutes = 5
    if interval.endswith("m"):
        try:
            minutes = max(1, int(interval[:-1]))
        except ValueError:
            minutes = 5
    complete_before = now_utc - timedelta(minutes=minutes)
    rows: list[dict[str, float | str]] = []
    for bar in parsed:
        ts = _as_dt(bar.ts_utc)
        if ts is None or ts > complete_before or ts.astimezone(ET).date() != session:
            continue
        rows.append({
            "ts": ts.isoformat(),
            "open": float(bar.open or 0.0),
            "high": float(bar.high or 0.0),
            "low": float(bar.low or 0.0),
            "close": float(bar.close or 0.0),
        })
    rows.sort(key=lambda row: str(row["ts"]))
    return symbol.upper(), rows


def fetch_session_bars(
    symbols: list[str],
    *,
    now: datetime,
    range_: str = "1d",
    interval: str = "5m",
    max_workers: int = 8,
) -> tuple[dict[str, list[dict[str, float | str]]], list[str]]:
    normalized = sorted({str(symbol).upper() for symbol in symbols if symbol})
    if not normalized:
        return {}, []
    output: dict[str, list[dict[str, float | str]]] = {}
    failed: list[str] = []
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(normalized)))) as pool:
        futures = {
            pool.submit(_fetch_symbol, symbol, now=now, range_=range_, interval=interval): symbol
            for symbol in normalized
        }
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                name, bars = future.result()
            except Exception:
                failed.append(symbol)
                continue
            if not bars:
                failed.append(symbol)
            else:
                output[name] = bars
    return output, sorted(set(failed))


def _symbols(base: dict[str, Any]) -> list[str]:
    values: set[str] = set()
    for member in (base.get("members") or {}).values():
        if not isinstance(member, dict):
            continue
        values.update(str(x).upper() for x in (member.get("positions") or {}))
        pending = member.get("pending_targets")
        if isinstance(pending, dict):
            values.update(str(x).upper() for x in pending)
    return sorted(values)


def _fresh_shadow(base: dict[str, Any], session: str) -> dict[str, Any]:
    members = copy.deepcopy(base.get("members") or {})
    strategy_members: list[dict[str, Any]] = []
    for member_id, member in members.items():
        if not isinstance(member, dict):
            continue
        member.setdefault("id", member_id)
        member.setdefault("trade_count", int(member.get("trades", 0) or 0))
        member.setdefault("gross_traded_notional", 0.0)
        member.setdefault("suppressed_rebalances", 0)
        if str(member.get("member_type") or "strategy") == "strategy":
            strategy_members.append(member)

    # A compact base generated before the intraday-shadow rollout did not expose
    # pending_targets. In that case OPEN execution cannot be reconstructed safely.
    # Keep the shadow observable, but mark the session explicitly non-comparable
    # rather than inventing fills or reporting false EOD divergences.
    pending_targets_contract_present = bool(strategy_members) and all(
        "pending_targets" in member for member in strategy_members
    )
    return {
        "schema_version": 2,
        "league_id": base.get("league_id"),
        "mode": "shadow_open_portfolio",
        "status": "INITIALIZING",
        "market_session": session,
        "last_eod_session": base.get("last_eod_session"),
        "source_base_state_hash": str(base.get("official_state_hash") or ""),
        "sessions": int(base.get("sessions", 0) or 0),
        "initial_nav_usd": float(base.get("initial_nav_usd", 10000.0) or 10000.0),
        "shadow_members": members,
        "shadow_open_fills": [],
        "shadow_exits": [],
        "open_fills_applied": False,
        "pending_targets_contract_present": pending_targets_contract_present,
        "shadow_evidence_complete": pending_targets_contract_present,
        "shadow_evidence_incomplete_reason": (
            None
            if pending_targets_contract_present
            else "legacy_live_base_missing_pending_targets"
        ),
        "observations": [],
        "live_execution_enabled": False,
        "counts_as_prospective_evidence": False,
        "automatic_promotion": False,
    }


def _compatible_previous(base: dict[str, Any], previous: dict[str, Any], session: str) -> bool:
    base_state_hash = str(base.get("official_state_hash") or "")
    return bool(
        base_state_hash
        and previous
        and previous.get("league_id") == base.get("league_id")
        and previous.get("market_session") == session
        and previous.get("last_eod_session") == base.get("last_eod_session")
        and str(previous.get("source_base_state_hash") or "") == base_state_hash
        and isinstance(previous.get("shadow_members"), dict)
    )


def _open_fill_bars(session_bars: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, float]]:
    return {
        symbol: {
            "open": float(rows[0].get("open", 0.0) or 0.0),
            "high": float(rows[0].get("high", 0.0) or 0.0),
            "low": float(rows[0].get("low", 0.0) or 0.0),
            "close": float(rows[0].get("close", 0.0) or 0.0),
        }
        for symbol, rows in session_bars.items()
        if rows and float(rows[0].get("open", 0.0) or 0.0) > 0
    }


def _apply_open_fills(
    shadow: dict[str, Any],
    base: dict[str, Any],
    execution_cfg: dict[str, Any],
    session_bars: dict[str, list[dict[str, Any]]],
) -> None:
    if shadow.get("open_fills_applied"):
        return
    open_bars = _open_fill_bars(session_bars)
    target_symbols = {
        str(symbol).upper()
        for member in (shadow.get("shadow_members") or {}).values()
        if isinstance(member, dict) and isinstance(member.get("pending_targets"), dict)
        for symbol in member.get("pending_targets", {})
    }
    missing = sorted(symbol for symbol in target_symbols if symbol not in open_bars)
    if missing:
        shadow["status"] = "DEGRADED"
        shadow["open_fill_status"] = "MISSING_OPEN_DATA"
        shadow["missing_open_symbols"] = missing
        return

    fills: list[dict[str, Any]] = []
    session_number = int(base.get("sessions", 0) or 0) + 1
    for member_id, member in (shadow.get("shadow_members") or {}).items():
        if not isinstance(member, dict):
            continue
        pending = member.get("pending_targets")
        if isinstance(pending, dict):
            before = len(fills)
            _execute_target(
                member,
                pending,
                open_bars,
                execution_cfg,
                fills,
                session=shadow["market_session"],
                session_number=session_number,
            )
            for trade in fills[before:]:
                trade["shadow_event"] = "OPEN_FILL"
                trade["idempotency_key"] = (
                    f'{shadow["market_session"]}:{member_id}:{trade.get("symbol")}:'
                    f'{trade.get("side")}:{trade.get("qty")}:{trade.get("fill_price")}'
                )
        member["pending_targets"] = None

    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for trade in fills:
        key = str(trade.get("idempotency_key") or "")
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        deduped.append(trade)
    shadow["shadow_open_fills"] = deduped
    shadow["open_fills_applied"] = True
    shadow["open_fill_status"] = "APPLIED"


def _apply_intraday_exits(
    shadow: dict[str, Any],
    execution_cfg: dict[str, Any],
    session_bars: dict[str, list[dict[str, Any]]],
) -> None:
    exits: list[dict[str, Any]] = list(shadow.get("shadow_exits") or [])
    for member_id, member in (shadow.get("shadow_members") or {}).items():
        if not isinstance(member, dict):
            continue
        for symbol in list((member.get("positions") or {}).keys()):
            position = (member.get("positions") or {}).get(symbol, {})
            qty = int(position.get("qty", 0) or 0)
            stop = float(position.get("stop_price", 0.0) or 0.0)
            take = float(position.get("take_profit_price", 0.0) or 0.0)
            if qty <= 0 or (stop <= 0 and take <= 0):
                continue
            rows = session_bars.get(symbol, [])
            exit_price = 0.0
            reason = ""
            touched_at = None
            for row in rows:
                low = float(row.get("low", 0.0) or 0.0)
                high = float(row.get("high", 0.0) or 0.0)
                open_price = float(row.get("open", 0.0) or 0.0)
                stop_hit = stop > 0 and low > 0 and low <= stop
                take_hit = take > 0 and high >= take
                if stop_hit:
                    exit_price = min(stop, open_price) if open_price > 0 else stop
                    reason = (
                        "stop_gap_through_at_open"
                        if open_price > 0 and open_price < stop
                        else "stop_touched_conservative_first"
                    )
                    touched_at = row.get("ts")
                    break
                if take_hit:
                    exit_price = max(take, open_price) if open_price > 0 else take
                    reason = (
                        "take_profit_gap_through_at_open"
                        if open_price > take
                        else "take_profit_touched"
                    )
                    touched_at = row.get("ts")
                    break
            if exit_price <= 0:
                continue
            before = len(exits)
            _trade(
                member,
                symbol,
                qty,
                exit_price,
                "SELL",
                execution_cfg,
                exits,
                reason,
            )
            if len(exits) > before:
                exits[-1]["shadow_event"] = "INTRADAY_EXIT"
                exits[-1]["touched_at"] = touched_at
                exits[-1]["idempotency_key"] = (
                    f'{shadow["market_session"]}:{member_id}:{symbol}:EXIT:{touched_at}:{reason}'
                )
    shadow["shadow_exits"] = exits


def _rows(
    shadow: dict[str, Any],
    base: dict[str, Any],
    session_bars: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    base_members = base.get("members") or {}
    for member_id, member in (shadow.get("shadow_members") or {}).items():
        if not isinstance(member, dict):
            continue
        latest_prices = {
            symbol: float(rows[-1].get("close", 0.0) or 0.0)
            for symbol, rows in session_bars.items()
            if rows
        }
        for symbol, position in (member.get("positions") or {}).items():
            if symbol in latest_prices:
                position["last_price"] = latest_prices[symbol]
        nav = _portfolio_nav(member, latest_prices)
        base_member = base_members.get(member_id, {}) if isinstance(base_members, dict) else {}
        eod_nav = float(base_member.get("eod_nav", nav) or nav)
        base_costs = float(base_member.get("costs_paid", 0.0) or 0.0)
        current_costs = float(member.get("costs_paid", base_costs) or base_costs)
        incremental_costs = max(0.0, current_costs - base_costs)
        net_pnl = nav - eod_nav
        gross_pnl = net_pnl + incremental_costs
        exposure = sum(
            int(pos.get("qty", 0) or 0) * float(latest_prices.get(sym, pos.get("last_price", 0.0)) or 0.0)
            for sym, pos in (member.get("positions") or {}).items()
        )
        positions: list[dict[str, Any]] = []
        for symbol, pos in sorted((member.get("positions") or {}).items()):
            price = float(latest_prices.get(symbol, pos.get("last_price", 0.0)) or 0.0)
            stop = float(pos.get("stop_price", 0.0) or 0.0)
            take = float(pos.get("take_profit_price", 0.0) or 0.0)
            positions.append({
                "symbol": symbol,
                "qty": int(pos.get("qty", 0) or 0),
                "entry": float(pos.get("cost_basis", 0.0) or 0.0),
                "last_price": price,
                "stop": stop or None,
                "take": take or None,
                "distance_to_stop": (price / stop - 1.0) if stop > 0 else None,
                "distance_to_take": (take / price - 1.0) if take > 0 and price > 0 else None,
            })
        result.append({
            "strategy": member_id,
            "member_type": member.get("member_type"),
            "nav": round(nav, 6),
            "eod_nav": round(eod_nav, 6),
            "return": nav / eod_nav - 1.0 if eod_nav > 0 else 0.0,
            "change_since_eod": nav / eod_nav - 1.0 if eod_nav > 0 else 0.0,
            "gross_pnl": round(gross_pnl, 6),
            "costs": round(incremental_costs, 6),
            "net_pnl": round(net_pnl, 6),
            "cash": round(float(member.get("cash", 0.0) or 0.0), 6),
            "gross_exposure": round(exposure, 6),
            "gross_exposure_pct": exposure / nav if nav > 0 else 0.0,
            "positions": positions,
            "trades": int(member.get("trade_count", 0) or 0),
            "costs_paid": current_costs,
        })
    strategies = sorted(result, key=lambda row: float(row.get("return", 0.0)), reverse=True)
    for idx, row in enumerate(strategies, start=1):
        row["rank"] = idx
    spy = next((row for row in result if row.get("strategy") == "benchmark_spy"), None)
    spy_return = float(spy.get("return", 0.0) or 0.0) if spy else 0.0
    for row in result:
        row["vs_spy"] = float(row.get("return", 0.0) or 0.0) - spy_return
    return result


def build_shadow_snapshot(
    base: dict[str, Any],
    previous: dict[str, Any],
    league_cfg: dict[str, Any],
    *,
    now: datetime,
    session_bars: dict[str, list[dict[str, Any]]],
    failed_symbols: list[str] | None = None,
) -> dict[str, Any]:
    now_et = now.astimezone(ET)
    session = now_et.date().isoformat()
    shadow = copy.deepcopy(previous) if _compatible_previous(base, previous, session) else _fresh_shadow(base, session)
    execution_cfg = dict(league_cfg.get("execution") or {})
    _apply_open_fills(shadow, base, execution_cfg, session_bars)
    if shadow.get("open_fills_applied"):
        _apply_intraday_exits(shadow, execution_cfg, session_bars)
    rows = _rows(shadow, base, session_bars)
    shadow["rows"] = rows
    shadow["generated_at"] = now.astimezone(timezone.utc).isoformat()
    shadow["status"] = "DEGRADED" if failed_symbols else ("LIVE" if shadow.get("open_fills_applied") else shadow.get("status", "INITIALIZING"))
    shadow["quote_source"] = {
        "provider": "Yahoo Finance chart",
        "interval": "5m",
        "required_symbols": len(_symbols(base)),
        "fresh_symbols": len(session_bars),
        "fresh_coverage": len(session_bars) / max(1, len(_symbols(base))),
        "failed_symbols": sorted(set(failed_symbols or [])),
    }
    observation = {
        "at": shadow["generated_at"],
        "market_session": session,
        "rows": [
            {
                "strategy": row.get("strategy"),
                "nav": row.get("nav"),
                "gross_pnl": row.get("gross_pnl"),
                "costs": row.get("costs"),
                "net_pnl": row.get("net_pnl"),
                "cash": row.get("cash"),
                "gross_exposure": row.get("gross_exposure"),
            }
            for row in rows
        ],
    }
    observations = [
        x for x in (shadow.get("observations") or [])
        if isinstance(x, dict) and x.get("market_session") == session
    ]
    if not observations or observations[-1].get("at") != observation["at"]:
        observations.append(observation)
    shadow["observations"] = observations[-32:]
    shadow["live_execution_enabled"] = False
    shadow["counts_as_prospective_evidence"] = False
    shadow["automatic_promotion"] = False
    return shadow


def run(
    *,
    base_path: Path,
    previous_path: Path,
    config_path: Path,
    output_path: Path,
    now: datetime | None = None,
    range_: str = "1d",
    interval: str = "5m",
    authoritative_eod_replay: bool = False,
) -> dict[str, Any]:
    base = _load(base_path)
    if base.get("status") != "READY":
        raise RuntimeError("Shadow portfolio requires READY compact EOD base")
    previous = _load(previous_path)
    league_cfg = _load(config_path)
    current = now or datetime.now(timezone.utc)
    current_et = current.astimezone(ET)
    configured_league_id = str(league_cfg.get("league_id") or "")
    base_league_id = str(base.get("league_id") or "")
    if not configured_league_id or base_league_id != configured_league_id:
        raise RuntimeError(
            "Shadow portfolio base league mismatch: "
            f"configured={configured_league_id!r} base={base_league_id!r}"
        )
    expected_base_session = previous_market_session(current_et.date()).isoformat()
    base_session = str(base.get("last_eod_session") or "")
    if not authoritative_eod_replay and base_session != expected_base_session:
        raise RuntimeError(
            "Shadow portfolio base is not T-1 authoritative state: "
            f"expected={expected_base_session} actual={base_session or 'missing'}"
        )
    if authoritative_eod_replay and not base_session:
        raise RuntimeError("Authoritative EOD replay base lacks last_eod_session")
    if not str(base.get("official_state_hash") or ""):
        raise RuntimeError("Shadow portfolio base lacks authoritative state hash")
    bars, failed = fetch_session_bars(_symbols(base), now=current, range_=range_, interval=interval)
    payload = build_shadow_snapshot(base, previous, league_cfg, now=current, session_bars=bars, failed_symbols=failed)
    _write(output_path, payload)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Build idempotent intraday Strategy League shadow portfolio")
    parser.add_argument("--base", default="data/metrics/strategy_league/live_base.json")
    parser.add_argument("--previous-snapshot", default="data/metrics/strategy_league/live_snapshot.json")
    parser.add_argument("--league-config", default="config/strategy_league.json")
    parser.add_argument("--output", default="data/metrics/strategy_league/live_snapshot.json")
    parser.add_argument("--range", default="1d")
    parser.add_argument("--interval", default="5m")
    parser.add_argument(
        "--authoritative-eod-replay",
        action="store_true",
        help=(
            "Replay the exact pre-EOD authoritative state even when its last session "
            "lags T-1. League identity and authoritative state hash remain mandatory."
        ),
    )
    args = parser.parse_args()
    payload = run(
        base_path=Path(args.base),
        previous_path=Path(args.previous_snapshot),
        config_path=Path(args.league_config),
        output_path=Path(args.output),
        range_=args.range,
        interval=args.interval,
        authoritative_eod_replay=bool(args.authoritative_eod_replay),
    )
    print(json.dumps({
        "status": payload.get("status"),
        "market_session": payload.get("market_session"),
        "open_fills": len(payload.get("shadow_open_fills") or []),
        "exits": len(payload.get("shadow_exits") or []),
        "observations": len(payload.get("observations") or []),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
