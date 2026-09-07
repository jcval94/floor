from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


CHALLENGER_ID = "capital_allocation_challenger"


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _load_history(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _source_list(spec: dict[str, Any]) -> list[str]:
    raw = spec.get("source_strategies")
    if isinstance(raw, list):
        values = sorted({str(item) for item in raw if item})
        if values:
            return values
    primary = str(spec.get("source_strategy") or "")
    return [primary] if primary else []


def _exposure_snapshot(state: dict[str, Any], member_id: str) -> dict[str, Any]:
    member = (state.get("members") or {}).get(member_id) or {}
    positions = member.get("positions") or {}
    cash = _number(member.get("cash"))
    position_rows: list[dict[str, Any]] = []
    gross_notional = 0.0
    stop_risk = 0.0
    for symbol, position in positions.items():
        qty = int(position.get("qty", 0) or 0)
        last = _number(position.get("last_price"))
        stop = _number(position.get("stop_price"))
        notional = max(qty, 0) * max(last, 0.0)
        risk = max(qty, 0) * max(last - stop, 0.0) if stop > 0 else 0.0
        gross_notional += notional
        stop_risk += risk
        position_rows.append(
            {
                "symbol": str(symbol),
                "qty": qty,
                "last_price": last,
                "notional": notional,
                "stop_price": stop if stop > 0 else None,
                "stop_risk_usd": risk,
            }
        )
    nav = cash + gross_notional
    for row in position_rows:
        row["weight"] = row["notional"] / nav if nav > 0 else None
        row["stop_risk_pct_nav"] = row["stop_risk_usd"] / nav if nav > 0 else None
    position_rows.sort(key=lambda row: row["notional"], reverse=True)
    return {
        "nav": nav,
        "cash": cash,
        "cash_pct_nav": cash / nav if nav > 0 else None,
        "gross_exposure_usd": gross_notional,
        "gross_exposure_pct_nav": gross_notional / nav if nav > 0 else None,
        "stop_heat_usd": stop_risk,
        "stop_heat_pct_nav": stop_risk / nav if nav > 0 else None,
        "max_position_pct_nav": max((row["weight"] or 0.0 for row in position_rows), default=0.0),
        "open_positions": len(position_rows),
        "positions": position_rows,
    }


def build_attribution_report(history_path: Path, *, member_id: str = CHALLENGER_ID) -> dict[str, Any]:
    history = _load_history(history_path)
    if not history:
        return {
            "schema_version": 1,
            "status": "WAITING",
            "member": member_id,
            "realized_trades": [],
            "source_attribution": [],
            "exposure": {},
        }

    previous_decisions: dict[str, Any] = {}
    books: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    realized: list[dict[str, Any]] = []
    final_state: dict[str, Any] = {}

    for record in history:
        session = str(record.get("session") or "")
        for trade in record.get("trades", []) or []:
            if not isinstance(trade, dict):
                continue
            member = str(trade.get("member") or "")
            symbol = str(trade.get("symbol") or "")
            if not member or not symbol:
                continue
            qty = int(trade.get("qty", 0) or 0)
            fill = _number(trade.get("fill_price"))
            costs = _number(trade.get("costs"))
            side = str(trade.get("side") or "").upper()
            if qty <= 0 or fill <= 0:
                continue

            book = books[member].get(symbol)
            if side == "BUY":
                spec = (((previous_decisions.get(member) or {}).get(symbol)) or {})
                if not isinstance(spec, dict):
                    spec = {}
                if book is None:
                    book = {
                        "qty": 0,
                        "avg_fill": 0.0,
                        "unallocated_buy_costs": 0.0,
                        "source_strategies": _source_list(spec),
                        "source_strategy": spec.get("source_strategy"),
                        "consensus_count": spec.get("consensus_count"),
                        "allocation_score": spec.get("score"),
                        "risk_budget_pct_nav": spec.get("risk_budget_pct_nav"),
                        "stop_risk_pct": spec.get("stop_risk_pct"),
                        "entry_session": session,
                    }
                old_qty = int(book["qty"])
                new_qty = old_qty + qty
                book["avg_fill"] = (
                    old_qty * _number(book["avg_fill"]) + qty * fill
                ) / max(new_qty, 1)
                book["qty"] = new_qty
                book["unallocated_buy_costs"] = _number(book["unallocated_buy_costs"]) + costs
                if not book.get("source_strategies"):
                    book["source_strategies"] = _source_list(spec)
                books[member][symbol] = book
                continue

            if side != "SELL" or book is None:
                continue
            owned_before = int(book.get("qty", 0) or 0)
            sold = min(qty, owned_before)
            if sold <= 0:
                continue
            avg_fill = _number(book.get("avg_fill"))
            buy_cost_pool = _number(book.get("unallocated_buy_costs"))
            buy_cost_alloc = buy_cost_pool * (sold / owned_before) if owned_before > 0 else 0.0
            gross_pnl = sold * (fill - avg_fill)
            net_pnl = gross_pnl - buy_cost_alloc - costs
            realized.append(
                {
                    "member": member,
                    "symbol": symbol,
                    "entry_session": book.get("entry_session"),
                    "exit_session": session,
                    "qty": sold,
                    "avg_entry_fill": avg_fill,
                    "exit_fill": fill,
                    "gross_pnl": gross_pnl,
                    "allocated_entry_costs": buy_cost_alloc,
                    "exit_costs": costs,
                    "net_pnl": net_pnl,
                    "reason": trade.get("reason"),
                    "source_strategy": book.get("source_strategy"),
                    "source_strategies": list(book.get("source_strategies") or []),
                    "consensus_count": book.get("consensus_count"),
                    "allocation_score": book.get("allocation_score"),
                    "risk_budget_pct_nav": book.get("risk_budget_pct_nav"),
                    "stop_risk_pct": book.get("stop_risk_pct"),
                }
            )
            remaining = owned_before - sold
            if remaining <= 0:
                books[member].pop(symbol, None)
            else:
                book["qty"] = remaining
                book["unallocated_buy_costs"] = max(0.0, buy_cost_pool - buy_cost_alloc)
                books[member][symbol] = book

        decisions = record.get("decisions_for_next_open")
        previous_decisions = decisions if isinstance(decisions, dict) else {}
        state = record.get("state_after")
        if isinstance(state, dict):
            final_state = state

    member_realized = [row for row in realized if row["member"] == member_id]
    source_pnl: dict[str, float] = defaultdict(float)
    source_trades: dict[str, int] = defaultdict(int)
    unattributed_pnl = 0.0
    for trade in member_realized:
        sources = list(trade.get("source_strategies") or [])
        if not sources:
            unattributed_pnl += _number(trade.get("net_pnl"))
            continue
        share = _number(trade.get("net_pnl")) / len(sources)
        for source in sources:
            source_pnl[source] += share
            source_trades[source] += 1

    attribution = [
        {
            "source_strategy": source,
            "net_pnl_equal_split": pnl,
            "realized_trade_participations": source_trades[source],
        }
        for source, pnl in sorted(source_pnl.items(), key=lambda item: item[1], reverse=True)
    ]
    total_net = sum(_number(row.get("net_pnl")) for row in member_realized)
    wins = sum(_number(row.get("net_pnl")) > 0 for row in member_realized)
    return {
        "schema_version": 1,
        "status": "OK",
        "member": member_id,
        "methodology": {
            "realized_pnl": "FIFO-like weighted-average position accounting from immutable league trade history",
            "source_attribution": "net P&L is split equally across source_strategies participating in the challenger signal",
            "capital_risk": "gross exposure and stop heat use final state quantities, last prices and configured stop prices",
        },
        "summary": {
            "realized_round_trips": len(member_realized),
            "realized_net_pnl": total_net,
            "wins": wins,
            "losses": len(member_realized) - wins,
            "win_rate": wins / len(member_realized) if member_realized else None,
            "unattributed_net_pnl": unattributed_pnl,
        },
        "source_attribution": attribution,
        "realized_trades": member_realized,
        "exposure": _exposure_snapshot(final_state, member_id),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Strategy League P&L and capital attribution")
    parser.add_argument("--history", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--member", default=CHALLENGER_ID)
    parser.add_argument("--allow-missing", action="store_true")
    args = parser.parse_args()
    history = Path(args.history)
    if not history.exists() and not args.allow_missing:
        raise SystemExit(f"history not found: {history}")
    report = build_attribution_report(history, member_id=args.member)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "member": report["member"], "summary": report.get("summary")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
