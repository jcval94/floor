from __future__ import annotations

import hashlib
import json
import math
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from contracts.strategy_contract import strategy_contract
from floor.calendar import is_market_session

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
    member["gross_traded_notional"] = float(
        member.get("gross_traded_notional", 0.0)
    ) + notional
    trades.append(
        {
            "member": member.get("id"),
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "notional": round(notional, 6),
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
    min_weight_delta = max(
        0.0,
        float(execution_cfg.get("min_rebalance_weight_delta", 0.0) or 0.0),
    )
    min_rebalance_notional = max(
        0.0,
        float(execution_cfg.get("min_rebalance_notional_usd", 0.0) or 0.0),
    )
    for symbol in sorted(current_symbols & set(desired_qty)):
        current = int(member.get("positions", {}).get(symbol, {}).get("qty", 0))
        desired = desired_qty.get(symbol, 0)
        price = open_prices.get(symbol, 0.0)
        if current <= 0 or desired <= 0 or price <= 0 or nav <= 0:
            continue
        current_weight = current * price / nav
        desired_weight = desired * price / nav
        delta_notional = abs(desired - current) * price
        if (
            abs(desired_weight - current_weight) < min_weight_delta
            or delta_notional < min_rebalance_notional
        ):
            if desired != current:
                member["suppressed_rebalances"] = int(
                    member.get("suppressed_rebalances", 0)
                ) + 1
            desired_qty[symbol] = current

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
        open_price = float(bar.get("open", 0.0) or 0.0)
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
            exit_price = (
                min(stop, open_price)
                if open_price > 0
                else stop
            )
            reason = (
                "stop_gap_through_at_open"
                if open_price > 0 and open_price < stop
                else "stop_touched_conservative_first"
            )
        elif take > 0 and high >= take:
            exit_price = (
                max(take, open_price)
                if open_price > 0
                else take
            )
            reason = (
                "take_profit_gap_through_at_open"
                if open_price > take
                else "take_profit_touched"
            )
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
    nav_values = [float(point.get("nav", 0.0)) for point in points if float(point.get("nav", 0.0)) > 0]
    average_nav = (
        sum(nav_values) / len(nav_values)
        if nav_values
        else initial_nav
    )
    costs = float(member.get("costs_paid", 0.0))
    gross_traded_notional = float(member.get("gross_traded_notional", 0.0))
    turnover = (
        gross_traded_notional / average_nav
        if average_nav > 0
        else 0.0
    )
    return {
        "nav": nav,
        "return": nav / initial_nav - 1.0,
        "sharpe": _sharpe(points),
        "max_drawdown": _max_drawdown(points),
        "trades": int(member.get("trade_count", 0)),
        "costs_paid": costs,
        "gross_traded_notional": gross_traded_notional,
        "average_nav": average_nav,
        "turnover": turnover,
        "cost_drag_bps_nav": (
            costs / average_nav * 10_000.0
            if average_nav > 0
            else 0.0
        ),
        "cost_bps_per_traded_notional": (
            costs / gross_traded_notional * 10_000.0
            if gross_traded_notional > 0
            else 0.0
        ),
        "suppressed_rebalances": int(member.get("suppressed_rebalances", 0)),
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
    review_window_sessions = max(
        1,
        int(review_cfg.get("turnover_review_window_sessions", review_cfg.get("min_sessions", 63))),
    )
    observed_sessions = max(1, int(state.get("session_count", 0)))
    member_specs = {
        str(spec.get("id") or ""): spec
        for spec in league_cfg.get("members", [])
        if isinstance(spec, dict) and str(spec.get("id") or "")
    }
    frozen_contract = state.get("frozen_contract")
    model_suite_frozen = (
        isinstance(frozen_contract, dict)
        and frozen_contract.get("model_suite_contract_version") == "v2"
        and all(
            str(frozen_contract.get(f"{task}_champion_sha256") or "")
            for task in ("d1", "w1", "q1", "value", "timing")
        )
    )
    evidence_contract = (
        "v2_model_suite_frozen"
        if model_suite_frozen
        else "legacy_v1_model_suite_unfrozen"
    )
    rows: list[dict[str, Any]] = []
    for member_id, member_metrics in metrics.items():
        ret = float(member_metrics["return"])
        member_spec = member_specs.get(member_id, {})
        declared_type = str(member_spec.get("type") or "")
        is_strategy = (
            declared_type == "strategy"
            or (not declared_type and not member_id.startswith("benchmark_"))
        )
        semantic_contract: dict[str, Any] = {}
        frozen_member_contracts = state.get("member_contracts")
        frozen_member_contract = (
            frozen_member_contracts.get(member_id)
            if isinstance(frozen_member_contracts, dict)
            else None
        )
        if isinstance(frozen_member_contract, dict):
            semantic_contract = dict(frozen_member_contract)
        elif is_strategy:
            try:
                semantic_contract = strategy_contract(member_id)
            except ValueError:
                # Unit-test/custom league members may intentionally be local.
                semantic_contract = {}
        evaluation_variant = str(
            member_spec.get("evaluation_variant")
            or semantic_contract.get("league_evaluation_variant")
            or "long_only_projection"
        )
        evidence_can_promote_canonical = bool(
            member_spec.get(
                "league_evidence_can_promote_canonical_variant",
                semantic_contract.get(
                    "league_evidence_can_promote_canonical_variant",
                    False,
                ),
            )
        )
        evidence_role = (
            str(
                member_spec.get("evidence_role")
                or semantic_contract.get("evidence_role")
                or "candidate"
            )
            if is_strategy
            else "benchmark"
        )
        strategy_promotion_enabled = (
            bool(
                member_spec.get(
                    "promotion_eligible",
                    semantic_contract.get("promotion_eligible", True),
                )
            )
            and evidence_role != "diagnostic_only"
            if is_strategy
            else False
        )
        max_gross_turnover = float(
            review_cfg.get(
                "max_gross_turnover_per_review_window",
                review_cfg.get(
                    "max_gross_turnover",
                    review_cfg.get("max_turnover", float("inf")),
                ),
            )
        )
        turnover_review_window = (
            float(member_metrics["turnover"])
            * review_window_sessions
            / observed_sessions
        )
        row = {
            "strategy": member_id,
            **member_metrics,
            "vs_spy": ret - float(spy_return) if spy_return is not None else None,
            "vs_equal_weight": (
                ret - float(equal_return) if equal_return is not None else None
            ),
            "evaluation_variant": evaluation_variant if is_strategy else "benchmark",
            "evidence_scope": "long_only" if is_strategy else "benchmark",
            "canonical_variant": (
                str(semantic_contract.get("canonical_variant") or "unspecified")
                if is_strategy
                else "benchmark"
            ),
            "evidence_contract": evidence_contract if is_strategy else "benchmark",
            "evidence_role": evidence_role,
            "strategy_promotion_enabled": strategy_promotion_enabled,
            "turnover_review_window_sessions": review_window_sessions,
            "turnover_review_window": turnover_review_window,
            "turnover_budget": (
                max_gross_turnover if is_strategy and math.isfinite(max_gross_turnover) else None
            ),
            "turnover_warning": (
                is_strategy
                and math.isfinite(max_gross_turnover)
                and turnover_review_window > max_gross_turnover
            ),
            "canonical_variant_promotion_eligible": False,
            "canonical_bidirectional_promotion_eligible": False,
            "promotion_review_eligible": False,
            "promotion_checks": {},
        }
        if is_strategy:
            checks = {
                "strategy_promotion_enabled": strategy_promotion_enabled,
                "model_suite_frozen": model_suite_frozen,
                "min_sessions": int(state.get("session_count", 0))
                >= int(review_cfg.get("min_sessions", 63)),
                "min_trades": int(member_metrics["trades"])
                >= int(review_cfg.get("min_trades", 10)),
                "max_drawdown": abs(float(member_metrics["max_drawdown"]))
                <= float(review_cfg.get("max_drawdown_abs", 0.15)),
                "min_sharpe": member_metrics["sharpe"] is not None
                and float(member_metrics["sharpe"])
                >= float(review_cfg.get("min_sharpe", 0.5)),
                "max_gross_turnover": (
                    not math.isfinite(max_gross_turnover)
                    or turnover_review_window <= max_gross_turnover
                ),
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
            row["canonical_variant_promotion_eligible"] = (
                evidence_can_promote_canonical
                and row["promotion_review_eligible"]
            )
            # Backward-compatible explicit field for the directional strategies.
            row["canonical_bidirectional_promotion_eligible"] = (
                str(row["canonical_variant"]) == "bidirectional"
                and bool(row["canonical_variant_promotion_eligible"])
            )
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
        "evidence_contract": evidence_contract,
        "model_suite_frozen": model_suite_frozen,
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


def next_market_session_after(session: str) -> str:
    current = date.fromisoformat(session)
    for offset in range(1, 15):
        candidate = current + timedelta(days=offset)
        if is_market_session(candidate):
            return candidate.isoformat()
    raise RuntimeError(f"Unable to resolve next market session after {session}")


def recover_legacy_compact_base(
    state_dir: Path,
    compact_base_path: Path,
) -> dict[str, Any]:
    """Recover exactly one lost EOD commit from the legacy compact cache.

    This compatibility bridge is intentionally restricted to schema-v1 bases.
    New schema-v2 compact bases are derived caches and may never advance the
    authoritative runtime history.
    """

    state = load_state(state_dir)
    if state is None:
        return {"status": "NO_AUTHORITATIVE_STATE"}
    if not compact_base_path.exists():
        return {"status": "NO_COMPACT_BASE"}

    base = json.loads(compact_base_path.read_text(encoding="utf-8"))
    if not isinstance(base, dict) or str(base.get("status") or "") != "READY":
        return {"status": "COMPACT_BASE_NOT_READY"}

    league_id = str(state.get("league_id") or "")
    if str(base.get("league_id") or "") != league_id:
        raise RuntimeError(
            "Compact Strategy League base belongs to another league: "
            f"runtime={league_id} compact={base.get('league_id')}"
        )

    runtime_session = str(state.get("last_session") or "")
    compact_session = str(base.get("last_eod_session") or "")
    if not runtime_session or not compact_session:
        raise RuntimeError("Strategy League recovery requires both runtime and compact sessions")
    if compact_session <= runtime_session:
        return {
            "status": "NOT_AHEAD",
            "runtime_session": runtime_session,
            "compact_session": compact_session,
        }

    if int(base.get("schema_version", 0) or 0) != 1:
        raise RuntimeError(
            "Refusing to promote a modern compact cache over authoritative runtime state"
        )

    expected_session = next_market_session_after(runtime_session)
    expected_count = int(state.get("session_count", 0) or 0) + 1
    compact_count = int(base.get("sessions", 0) or 0)
    if compact_session != expected_session or compact_count != expected_count:
        raise RuntimeError(
            "Legacy compact recovery is only allowed for exactly one missing market session: "
            f"runtime={runtime_session}/{state.get('session_count')} "
            f"expected={expected_session}/{expected_count} "
            f"compact={compact_session}/{compact_count}"
        )

    state_members = state.get("members")
    base_members = base.get("members")
    if not isinstance(state_members, dict) or not isinstance(base_members, dict):
        raise RuntimeError("Strategy League recovery requires member dictionaries")
    if set(state_members) != set(base_members):
        raise RuntimeError(
            "Compact/runtime member sets differ; automatic recovery is unsafe"
        )

    recovered = json.loads(json.dumps(state, ensure_ascii=False))
    for member_id, prior in state_members.items():
        compact = base_members.get(member_id)
        if not isinstance(prior, dict) or not isinstance(compact, dict):
            raise RuntimeError(f"Invalid member payload during recovery: {member_id}")

        cash = float(compact.get("cash", 0.0) or 0.0)
        if not math.isfinite(cash) or cash < -1e-6:
            raise RuntimeError(f"Invalid recovered cash for {member_id}: {cash}")

        positions_raw = compact.get("positions", {})
        if not isinstance(positions_raw, dict):
            raise RuntimeError(f"Invalid recovered positions for {member_id}")
        positions: dict[str, dict[str, Any]] = {}
        marked_value = 0.0
        for symbol, raw_position in positions_raw.items():
            if not isinstance(raw_position, dict):
                raise RuntimeError(
                    f"Invalid recovered position payload for {member_id}/{symbol}"
                )
            qty = int(raw_position.get("qty", 0) or 0)
            last_price = float(raw_position.get("last_price", 0.0) or 0.0)
            cost_basis = float(raw_position.get("cost_basis", 0.0) or 0.0)
            if qty <= 0 or last_price <= 0 or cost_basis <= 0:
                raise RuntimeError(
                    f"Invalid recovered position for {member_id}/{symbol}: "
                    f"qty={qty} last={last_price} basis={cost_basis}"
                )
            if not all(math.isfinite(value) for value in (last_price, cost_basis)):
                raise RuntimeError(
                    f"Non-finite recovered position for {member_id}/{symbol}"
                )
            positions[str(symbol)] = dict(raw_position)
            marked_value += qty * last_price

        eod_nav = float(compact.get("eod_nav", 0.0) or 0.0)
        if not math.isfinite(eod_nav) or eod_nav <= 0:
            raise RuntimeError(f"Invalid recovered EOD NAV for {member_id}: {eod_nav}")
        reconstructed_nav = cash + marked_value
        nav_tolerance = max(0.05, abs(eod_nav) * 1e-6)
        if abs(reconstructed_nav - eod_nav) > nav_tolerance:
            raise RuntimeError(
                f"Recovered NAV invariant failed for {member_id}: "
                f"compact={eod_nav} reconstructed={reconstructed_nav}"
            )

        for field in (
            "trade_count",
            "gross_traded_notional",
            "suppressed_rebalances",
            "costs_paid",
        ):
            recovered_value = float(compact.get(field, 0.0) or 0.0)
            prior_value = float(prior.get(field, 0.0) or 0.0)
            if not math.isfinite(recovered_value) or recovered_value + 1e-9 < prior_value:
                raise RuntimeError(
                    f"Recovered cumulative counter regressed for {member_id}/{field}: "
                    f"prior={prior_value} recovered={recovered_value}"
                )

        daily_nav = [
            dict(row)
            for row in prior.get("daily_nav", [])
            if isinstance(row, dict)
        ]
        if daily_nav and str(daily_nav[-1].get("session") or "") >= compact_session:
            raise RuntimeError(
                f"Recovered daily NAV would rewrite history for {member_id}"
            )
        daily_nav.append({"session": compact_session, "nav": eod_nav})

        recovered_member = recovered["members"][member_id]
        recovered_member.update(
            {
                "cash": cash,
                "positions": positions,
                "pending_targets": compact.get("pending_targets"),
                "daily_nav": daily_nav,
                "trade_count": int(compact.get("trade_count", 0) or 0),
                "costs_paid": float(compact.get("costs_paid", 0.0) or 0.0),
                "gross_traded_notional": float(
                    compact.get("gross_traded_notional", 0.0) or 0.0
                ),
                "suppressed_rebalances": int(
                    compact.get("suppressed_rebalances", 0) or 0
                ),
            }
        )

    recovered["last_session"] = compact_session
    recovered["session_count"] = compact_count
    recovery = {
        "reason": "legacy_compact_published_before_runtime_commit",
        "source": "strategy-live-base-v1",
        "source_schema_version": 1,
        "source_sha256": sha256_file(compact_base_path),
        "source_generated_at": base.get("generated_at"),
        "recovered_from_session": runtime_session,
        "recovered_to_session": compact_session,
    }
    _append_record(
        state_dir,
        "RECOVERY_COMPACT_EOD",
        recovered,
        [],
        {"recovery": recovery},
    )
    return {
        "status": "RECOVERED",
        **recovery,
        "session_count": compact_count,
        "audit_hash": recovered.get("last_hash"),
    }


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
            "gross_traded_notional": 0.0,
            "suppressed_rebalances": 0,
        }
    member_contracts = {
        str(spec.get("id") or ""): {
            "evaluation_variant": spec.get("evaluation_variant"),
            "league_evidence_can_promote_canonical_variant": bool(
                spec.get("league_evidence_can_promote_canonical_variant", False)
            ),
            "canonical_variant": spec.get("canonical_variant"),
            "evidence_role": spec.get("evidence_role"),
            "promotion_eligible": spec.get("promotion_eligible"),
        }
        for spec in league_cfg.get("members", [])
        if isinstance(spec, dict)
        and spec.get("type") == "strategy"
        and str(spec.get("id") or "")
    }
    state = {
        "schema_version": 1,
        "league_id": str(league_cfg["league_id"]),
        "start_session": session,
        "last_session": session,
        "session_count": 1,
        "frozen_contract": dict(frozen_contract),
        "member_contracts": member_contracts,
        "members": members,
        "last_hash": "",
    }
    _append_record(state_dir, "GENESIS", state, [], initial_targets)
    return state


def transition_research_model_epoch(
    state_dir: Path,
    state: dict,
    new_frozen_contract: dict[str, str],
    *,
    next_fold_start: str,
    fold_index: int,
) -> dict:
    """Change the frozen model suite without liquidating a research portfolio.

    This is intentionally separate from production/shadow Strategy League
    advancement. Walk-forward research retrains models between folds, but a
    broker account does not reset cash, positions, costs, or trade counters when
    the model version changes.
    """

    old_contract = state.get("frozen_contract")
    if old_contract == new_frozen_contract:
        return state
    transition = {
        "fold": int(fold_index),
        "next_fold_start": str(next_fold_start),
        "from_contract": dict(old_contract) if isinstance(old_contract, dict) else {},
        "to_contract": dict(new_frozen_contract),
    }
    state["frozen_contract"] = dict(new_frozen_contract)
    state.setdefault("model_epoch_transitions", []).append(transition)
    _append_record(
        state_dir,
        "MODEL_EPOCH_TRANSITION",
        state,
        [],
        {"model_epoch_transition": transition},
    )
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
