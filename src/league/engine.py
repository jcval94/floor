from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any


STRATEGY_MEMBERS = {
    "weekly_opportunity_ridge",
    "breakout_protected_by_floor",
    "capital_allocation_challenger",
}


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fill_price(raw_price: float, execution_cfg: dict, side: str) -> float:
    slippage = float(execution_cfg.get("slippage_bps", 0.0)) / 10000.0
    return raw_price * (1.0 + slippage if side == "BUY" else 1.0 - slippage)


def _portfolio_nav(member: dict, prices: dict[str, float]) -> float:
    nav = float(member.get("cash", 0.0))
    for symbol, position in member.get("positions", {}).items():
        fallback = float(position.get("last_price", 0.0) or 0.0)
        nav += int(position.get("qty", 0)) * float(prices.get(symbol, fallback))
    return nav


def _trade(
    member: dict,
    symbol: str,
    qty: int,
    raw_price: float,
    side: str,
    execution_cfg: dict,
    trades: list[dict],
    reason: str,
) -> int:
    if qty <= 0 or raw_price <= 0:
        return 0
    positions = member.setdefault("positions", {})
    current = positions.get(symbol, {})
    owned = int(current.get("qty", 0))
    if side == "SELL":
        qty = min(qty, owned)
    if qty <= 0:
        return 0

    fill = _fill_price(raw_price, execution_cfg, side)
    commission_rate = float(execution_cfg.get("commission_bps", 0.0)) / 10000.0
    sell_fee_rate = (
        float(execution_cfg.get("sell_fee_bps", 0.0)) / 10000.0
        if side == "SELL"
        else 0.0
    )
    notional = qty * fill
    commission = notional * commission_rate
    sell_fee = notional * sell_fee_rate

    if side == "BUY":
        unit_total = fill * (1.0 + commission_rate)
        affordable = int(float(member.get("cash", 0.0)) / max(unit_total, 1e-9))
        qty = min(qty, affordable)
        if qty <= 0:
            return 0
        notional = qty * fill
        commission = notional * commission_rate
        sell_fee = 0.0
        member["cash"] = float(member.get("cash", 0.0)) - notional - commission
        old_basis = float(current.get("cost_basis", fill))
        new_qty = owned + qty
        positions[symbol] = {
            **current,
            "qty": new_qty,
            "cost_basis": ((owned * old_basis) + (qty * fill)) / max(new_qty, 1),
            "last_price": raw_price,
        }
    else:
        member["cash"] = float(member.get("cash", 0.0)) + notional - commission - sell_fee
        remaining = owned - qty
        if remaining <= 0:
            positions.pop(symbol, None)
        else:
            current["qty"] = remaining
            current["last_price"] = raw_price
            positions[symbol] = current

    slippage_cost = abs(fill - raw_price) * qty
    total_cost = commission + sell_fee + slippage_cost
    member["trade_count"] = int(member.get("trade_count", 0)) + 1
    member["costs_paid"] = float(member.get("costs_paid", 0.0)) + total_cost
    trades.append(
        {
            "member": member.get("id"),
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "raw_price": round(raw_price, 6),
            "fill_price": round(fill, 6),
            "costs": round(total_cost, 6),
            "reason": reason,
        }
    )
    return qty


def _execute_target(
    member: dict,
    target: dict[str, dict],
    bars: dict[str, dict],
    execution_cfg: dict,
    trades: list[dict],
    *,
    session: str,
    session_number: int,
) -> None:
    open_prices = {
        symbol: float(bar.get("open", 0.0) or 0.0)
        for symbol, bar in bars.items()
        if float(bar.get("open", 0.0) or 0.0) > 0
    }
    nav = _portfolio_nav(member, open_prices)
    desired_qty: dict[str, int] = {}
    for symbol, spec in target.items():
        price = open_prices.get(symbol, 0.0)
        weight = max(0.0, min(1.0, float(spec.get("weight", 0.0))))
        if price > 0:
            desired_qty[symbol] = int((nav * weight) / price)

    current_symbols = set(member.get("positions", {}))
    for symbol in sorted(current_symbols | set(desired_qty)):
        current = int(member.get("positions", {}).get(symbol, {}).get("qty", 0))
        desired = desired_qty.get(symbol, 0)
        if desired < current:
            _trade(
                member,
                symbol,
                current - desired,
                open_prices.get(symbol, 0.0),
                "SELL",
                execution_cfg,
                trades,
                "rebalance_at_next_open",
            )

    for symbol in sorted(desired_qty):
        current = int(member.get("positions", {}).get(symbol, {}).get("qty", 0))
        desired = desired_qty[symbol]
        if desired > current:
            filled = _trade(
                member,
                symbol,
                desired - current,
                open_prices.get(symbol, 0.0),
                "BUY",
                execution_cfg,
                trades,
                "signal_t_to_open_t_plus_1",
            )
            if filled > 0 and current == 0:
                position = member.get("positions", {}).get(symbol)
                if position is not None:
                    position["entry_session"] = session
                    position["entry_session_number"] = session_number
        position = member.get("positions", {}).get(symbol)
        if position is not None:
            spec = target.get(symbol, {})
            position["stop_price"] = spec.get("stop_price")
            position["take_profit_price"] = spec.get("take_profit_price")


def _apply_strategy_exits(
    member: dict,
    bars: dict[str, dict],
    execution_cfg: dict,
    trades: list[dict],
    *,
    force_close: bool,
    current_session_number: int,
    max_holding_sessions: int = 0,
) -> None:
    for symbol in list(member.get("positions", {})):
        position = member["positions"].get(symbol, {})
        qty = int(position.get("qty", 0))
        bar = bars.get(symbol, {})
        low = float(bar.get("low", 0.0) or 0.0)
        high = float(bar.get("high", 0.0) or 0.0)
        close = float(bar.get("close", 0.0) or 0.0)
        stop = float(position.get("stop_price", 0.0) or 0.0)
        take = float(position.get("take_profit_price", 0.0) or 0.0)
        entry_number = int(position.get("entry_session_number", 0) or 0)
        held_sessions = (
            current_session_number - entry_number + 1
            if entry_number > 0 and current_session_number >= entry_number
            else 0
        )
        timeout_due = (
            max_holding_sessions > 0 and held_sessions >= max_holding_sessions
        )

        exit_price = 0.0
        reason = ""
        if stop > 0 and low > 0 and low <= stop:
            exit_price = stop
            reason = "stop_touched_conservative_first"
        elif take > 0 and high >= take:
            exit_price = take
            reason = "take_profit_touched"
        elif timeout_due and close > 0:
            exit_price = close
            reason = f"max_holding_sessions_{max_holding_sessions}"
        elif force_close and close > 0:
            exit_price = close
            reason = "strategy_session_timeout"
        if exit_price > 0:
            _trade(
                member,
                symbol,
                qty,
                exit_price,
                "SELL",
                execution_cfg,
                trades,
                reason,
            )


def _mark_member(member: dict, session: str, bars: dict[str, dict]) -> float:
    prices = {
        symbol: float(bar.get("close", 0.0) or 0.0)
        for symbol, bar in bars.items()
        if float(bar.get("close", 0.0) or 0.0) > 0
    }
    for symbol, position in member.get("positions", {}).items():
        if symbol in prices:
            position["last_price"] = prices[symbol]
    nav = _portfolio_nav(member, prices)
    member.setdefault("daily_nav", []).append(
        {"session": session, "nav": round(nav, 6)}
    )
    return nav


def _returns(points: list[dict]) -> list[float]:
    values = [float(point.get("nav", 0.0)) for point in points]
    return [
        values[idx] / values[idx - 1] - 1.0
        for idx in range(1, len(values))
        if values[idx - 1] > 0
    ]


def _sharpe(points: list[dict]) -> float | None:
    values = _returns(points)
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    std = math.sqrt(max(variance, 0.0))
    return mean / std * math.sqrt(252.0) if std > 1e-12 else None


def _max_drawdown(points: list[dict]) -> float:
    peak = 0.0
    worst = 0.0
    for point in points:
        nav = float(point.get("nav", 0.0))
        peak = max(peak, nav)
        if peak > 0:
            worst = min(worst, nav / peak - 1.0)
    return worst


def _member_metrics(member: dict, initial_nav: float) -> dict[str, Any]:
    points = member.get("daily_nav", [])
    nav = float(points[-1]["nav"]) if points else initial_nav
    costs = float(member.get("costs_paid", 0.0))
    return {
        "nav": nav,
        "return": nav / initial_nav - 1.0,
        "sharpe": _sharpe(points),
        "max_drawdown": _max_drawdown(points),
        "trades": int(member.get("trade_count", 0)),
        "costs_paid": costs,
        "nav_if_2x_costs_estimate": nav - costs,
        "nav_if_3x_costs_estimate": nav - 2.0 * costs,
        "equity_curve": list(points),
    }


def build_leaderboard(state: dict, league_cfg: dict) -> dict[str, Any]:
    initial_nav = float(league_cfg["initial_nav_usd"])
    metrics = {
        member_id: _member_metrics(member, initial_nav)
        for member_id, member in state["members"].items()
    }
    spy_return = metrics.get("benchmark_spy", {}).get("return")
    equal_return = metrics.get("benchmark_equal_weight", {}).get("return")
    review_cfg = league_cfg.get("promotion_review", {})
    rows: list[dict[str, Any]] = []
    for member_id, member_metrics in metrics.items():
        ret = float(member_metrics["return"])
        row = {
            "strategy": member_id,
            **member_metrics,
            "vs_spy": ret - float(spy_return) if spy_return is not None else None,
            "vs_equal_weight": (
                ret - float(equal_return) if equal_return is not None else None
            ),
            "promotion_review_eligible": False,
            "promotion_checks": {},
        }
        if member_id in STRATEGY_MEMBERS:
            checks = {
                "min_sessions": int(state.get("session_count", 0))
                >= int(review_cfg.get("min_sessions", 63)),
                "min_trades": int(member_metrics["trades"])
                >= int(review_cfg.get("min_trades", 10)),
                "max_drawdown": abs(float(member_metrics["max_drawdown"]))
                <= float(review_cfg.get("max_drawdown_abs", 0.15)),
                "min_sharpe": member_metrics["sharpe"] is not None
                and float(member_metrics["sharpe"])
                >= float(review_cfg.get("min_sharpe", 0.5)),
                "positive_excess_vs_spy": row["vs_spy"] is not None
                and float(row["vs_spy"]) > 0,
                "positive_excess_vs_equal_weight": row["vs_equal_weight"] is not None
                and float(row["vs_equal_weight"]) > 0,
                "positive_after_3x_cost_estimate": float(
                    member_metrics["nav_if_3x_costs_estimate"]
                )
                > initial_nav,
            }
            row["promotion_checks"] = checks
            row["promotion_review_eligible"] = all(checks.values())
        rows.append(row)
    rows.sort(key=lambda item: float(item.get("return", 0.0)), reverse=True)
    return {
        "schema_version": 1,
        "league_id": state["league_id"],
        "mode": "shadow_paper",
        "status": "RUNNING",
        "start_session": state["start_session"],
        "last_session": state["last_session"],
        "sessions": state["session_count"],
        "initial_nav_usd": initial_nav,
        "automatic_promotion": False,
        "live_execution_enabled": False,
        "rows": rows,
        "audit_hash": state.get("last_hash"),
    }


def _validate_history(history_path: Path) -> tuple[dict | None, str]:
    if not history_path.exists():
        return None, ""
    previous = ""
    last_state: dict | None = None
    for line_number, line in enumerate(
        history_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        record = json.loads(line)
        if str(record.get("prev_hash") or "") != previous:
            raise RuntimeError(
                f"Strategy League audit chain broken at line {line_number}: prev_hash mismatch"
            )
        expected = str(record.get("record_hash") or "")
        unsigned = dict(record)
        unsigned.pop("record_hash", None)
        actual = sha256_text(_canonical_json(unsigned))
        if expected != actual:
            raise RuntimeError(
                f"Strategy League audit chain broken at line {line_number}: hash mismatch"
            )
        previous = expected
        state_after = record.get("state_after")
        last_state = dict(state_after) if isinstance(state_after, dict) else None
    if last_state is not None:
        last_state["last_hash"] = previous
    return last_state, previous


def load_state(state_dir: Path) -> dict | None:
    state, _ = _validate_history(state_dir / "history.jsonl")
    return state


def _append_record(
    state_dir: Path,
    event: str,
    state: dict,
    trades: list[dict],
    decisions: dict[str, Any],
) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    history_path = state_dir / "history.jsonl"
    _, previous = _validate_history(history_path)
    state_snapshot = json.loads(json.dumps(state, ensure_ascii=False))
    state_snapshot["last_hash"] = previous
    unsigned = {
        "event": event,
        "session": state["last_session"],
        "prev_hash": previous,
        "trades": trades,
        "decisions_for_next_open": decisions,
        "state_after": state_snapshot,
    }
    record_hash = sha256_text(_canonical_json(unsigned))
    record = {**unsigned, "record_hash": record_hash}
    with history_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    state["last_hash"] = record_hash
    tmp = state_dir / "state.json.tmp"
    tmp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    tmp.replace(state_dir / "state.json")


def initialize_league(
    state_dir: Path,
    league_cfg: dict,
    session: str,
    frozen_contract: dict[str, str],
    initial_targets: dict[str, dict[str, dict]],
) -> dict:
    existing = load_state(state_dir)
    if existing is not None:
        return existing
    initial_nav = float(league_cfg["initial_nav_usd"])
    members = {}
    for spec in league_cfg.get("members", []):
        member_id = str(spec["id"])
        members[member_id] = {
            "id": member_id,
            "cash": initial_nav,
            "positions": {},
            "pending_targets": initial_targets.get(member_id),
            "daily_nav": [{"session": session, "nav": initial_nav}],
            "trade_count": 0,
            "costs_paid": 0.0,
        }
    state = {
        "schema_version": 1,
        "league_id": str(league_cfg["league_id"]),
        "start_session": session,
        "last_session": session,
        "session_count": 1,
        "frozen_contract": dict(frozen_contract),
        "members": members,
        "last_hash": "",
    }
    _append_record(state_dir, "GENESIS", state, [], initial_targets)
    return state


def advance_league(
    state_dir: Path,
    state: dict,
    league_cfg: dict,
    session: str,
    bars: dict[str, dict],
    frozen_contract: dict[str, str],
    next_targets: dict[str, dict[str, dict]],
) -> dict:
    if session <= str(state.get("last_session") or ""):
        return state
    if state.get("frozen_contract") != frozen_contract:
        raise RuntimeError(
            "Strategy League frozen contract changed; create a new league_id instead of rewriting history"
        )

    execution_cfg = league_cfg.get("execution", {})
    max_holding_by_strategy = league_cfg.get("strategy_max_holding_sessions", {})
    current_session_number = int(state.get("session_count", 0)) + 1
    trades: list[dict] = []
    for member_id, member in state["members"].items():
        pending = member.get("pending_targets")
        if isinstance(pending, dict):
            _execute_target(
                member,
                pending,
                bars,
                execution_cfg,
                trades,
                session=session,
                session_number=current_session_number,
            )
        member["pending_targets"] = None

        if member_id in max_holding_by_strategy:
            max_holding_sessions = int(
                max_holding_by_strategy.get(member_id, 0) or 0
            )
            _apply_strategy_exits(
                member,
                bars,
                execution_cfg,
                trades,
                force_close=False,
                current_session_number=current_session_number,
                max_holding_sessions=max_holding_sessions,
            )
        elif member_id == "breakout_protected_by_floor":
            _apply_strategy_exits(
                member,
                bars,
                execution_cfg,
                trades,
                force_close=True,
                current_session_number=current_session_number,
            )
        _mark_member(member, session, bars)

    for member_id, target in next_targets.items():
        if member_id in state["members"]:
            state["members"][member_id]["pending_targets"] = target

    state["last_session"] = session
    state["session_count"] = current_session_number
    _append_record(state_dir, "EOD", state, trades, next_targets)
    return state


def write_leaderboard(
    state_dir: Path,
    state: dict,
    league_cfg: dict,
) -> dict[str, Any]:
    payload = build_leaderboard(state, league_cfg)
    path = state_dir / "leaderboard.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return payload
