from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _latest_history(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    rows = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return json.loads(rows[-1]) if rows else {}


def _key(row: dict[str, Any]) -> tuple[str, str, str, int, str]:
    return (
        str(row.get("member") or ""),
        str(row.get("symbol") or ""),
        str(row.get("side") or ""),
        int(row.get("qty", 0) or 0),
        str(row.get("reason") or ""),
    )


def reconcile(
    *,
    shadow_path: Path,
    history_path: Path,
    output_path: Path,
    price_tolerance_bps: float = 8.0,
    cost_tolerance_usd: float = 0.05,
) -> dict[str, Any]:
    shadow = _load(shadow_path)
    official = _latest_history(history_path)
    official_trades = [row for row in official.get("trades", []) if isinstance(row, dict)]
    shadow_trades = [
        row for row in list(shadow.get("shadow_open_fills") or []) + list(shadow.get("shadow_exits") or [])
        if isinstance(row, dict)
    ]

    official_by_key = {_key(row): row for row in official_trades}
    divergences: list[dict[str, Any]] = []
    explained_differences: list[dict[str, Any]] = []
    matched_official_keys: set[tuple[str, str, str, int, str]] = set()
    matched = 0
    exit_reasons = {
        "stop_touched_conservative_first",
        "stop_gap_through_at_open",
        "take_profit_touched",
        "take_profit_gap_through_at_open",
    }
    for row in shadow_trades:
        key = _key(row)
        peer = official_by_key.get(key)
        if peer is None and str(row.get("side") or "") == "SELL" and str(row.get("reason") or "") in exit_reasons:
            candidates = [
                candidate
                for candidate in official_trades
                if str(candidate.get("member") or "") == str(row.get("member") or "")
                and str(candidate.get("symbol") or "") == str(row.get("symbol") or "")
                and str(candidate.get("side") or "") == "SELL"
                and int(candidate.get("qty", 0) or 0) == int(row.get("qty", 0) or 0)
                and str(candidate.get("reason") or "") in exit_reasons
            ]
            if len(candidates) == 1:
                peer = candidates[0]
                explained_differences.append(
                    {
                        "kind": "intraday_path_vs_daily_ohlc_exit",
                        "member": row.get("member"),
                        "symbol": row.get("symbol"),
                        "shadow_reason": row.get("reason"),
                        "official_reason": peer.get("reason"),
                        "shadow_fill_price": row.get("fill_price"),
                        "official_fill_price": peer.get("fill_price"),
                        "explanation": (
                            "5m point-in-time ordering resolves stop/take path while the frozen "
                            "v11 daily OHLC contract uses conservative stop-first semantics."
                        ),
                    }
                )
        if peer is None:
            divergences.append({"kind": "missing_official_trade", "shadow": row})
            continue
        matched_official_keys.add(_key(peer))
        matched += 1
        shadow_price = float(row.get("fill_price", 0.0) or 0.0)
        official_price = float(peer.get("fill_price", 0.0) or 0.0)
        price_bps = abs(shadow_price / official_price - 1.0) * 10000 if official_price > 0 else 0.0
        cost_delta = abs(float(row.get("costs", 0.0) or 0.0) - float(peer.get("costs", 0.0) or 0.0))
        path_explained = (
            str(row.get("side") or "") == "SELL"
            and str(row.get("reason") or "") in exit_reasons
            and str(peer.get("reason") or "") in exit_reasons
            and str(row.get("reason") or "") != str(peer.get("reason") or "")
        )
        if (price_bps > price_tolerance_bps or cost_delta > cost_tolerance_usd) and not path_explained:
            divergences.append({
                "kind": "trade_mismatch",
                "key": key,
                "price_bps": price_bps,
                "cost_delta_usd": cost_delta,
                "shadow": row,
                "official": peer,
            })

    # Official close/holding-timeout exits can be EOD-only by v11 contract.
    # They are recorded but are not treated as shadow divergence.
    official_only = [
        row for row in official_trades
        if _key(row) not in matched_official_keys
    ]
    unexplained_official = [
        row for row in official_only
        if not (
            str(row.get("reason") or "").startswith("max_holding_sessions_")
            or str(row.get("reason") or "") == "strategy_session_timeout"
        )
    ]
    for row in unexplained_official:
        divergences.append({"kind": "unexpected_official_only_trade", "official": row})

    payload = {
        "schema_version": 1,
        "status": "RECONCILED" if not divergences else "DIVERGED",
        "market_session": shadow.get("market_session"),
        "official_session": official.get("session"),
        "shadow_trade_count": len(shadow_trades),
        "official_trade_count": len(official_trades),
        "matched_trade_count": matched,
        "allowed_eod_only_trade_count": len(official_only) - len(unexplained_official),
        "price_tolerance_bps": price_tolerance_bps,
        "cost_tolerance_usd": cost_tolerance_usd,
        "divergences": divergences,
        "explained_differences": explained_differences,
        "note": "v11 keeps EOD as official owner; source-granularity exit differences and EOD-only max-holding/session-timeout exits are documented until v12.",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if divergences:
        raise RuntimeError(f"Material shadow/EOD divergence: {len(divergences)}")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Fail-closed reconciliation between intraday shadow and official EOD")
    parser.add_argument("--shadow", default="data/metrics/strategy_league/live_snapshot.json")
    parser.add_argument("--history", required=True)
    parser.add_argument("--output", default="data/metrics/strategy_league/shadow_eod_reconciliation.json")
    parser.add_argument("--price-tolerance-bps", type=float, default=8.0)
    parser.add_argument("--cost-tolerance-usd", type=float, default=0.05)
    args = parser.parse_args()
    payload = reconcile(
        shadow_path=Path(args.shadow),
        history_path=Path(args.history),
        output_path=Path(args.output),
        price_tolerance_bps=args.price_tolerance_bps,
        cost_tolerance_usd=args.cost_tolerance_usd,
    )
    print(json.dumps(payload, sort_keys=True))


if __name__ == "__main__":
    main()
