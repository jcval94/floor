from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


EXIT_REASONS = {
    "stop_touched_conservative_first",
    "stop_gap_through_at_open",
    "take_profit_touched",
    "take_profit_gap_through_at_open",
}
EOD_ONLY_EXIT_PREFIXES = ("max_holding_sessions_",)
EOD_ONLY_EXIT_REASONS = {"strategy_session_timeout"}


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


def _same_trade_identity(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        str(left.get("member") or "") == str(right.get("member") or "")
        and str(left.get("symbol") or "") == str(right.get("symbol") or "")
        and str(left.get("side") or "") == str(right.get("side") or "")
        and int(left.get("qty", 0) or 0) == int(right.get("qty", 0) or 0)
    )


def _is_exit(row: dict[str, Any]) -> bool:
    return (
        str(row.get("side") or "") == "SELL"
        and str(row.get("reason") or "") in EXIT_REASONS
    )


def _is_eod_only_exit(row: dict[str, Any]) -> bool:
    reason = str(row.get("reason") or "")
    return (
        reason in EOD_ONLY_EXIT_REASONS
        or any(reason.startswith(prefix) for prefix in EOD_ONLY_EXIT_PREFIXES)
    )


def _price_and_cost_delta(
    shadow: dict[str, Any],
    official: dict[str, Any],
) -> tuple[float, float]:
    shadow_price = float(shadow.get("fill_price", 0.0) or 0.0)
    official_price = float(official.get("fill_price", 0.0) or 0.0)
    price_bps = (
        abs(shadow_price / official_price - 1.0) * 10000
        if official_price > 0
        else 0.0
    )
    cost_delta = abs(
        float(shadow.get("costs", 0.0) or 0.0)
        - float(official.get("costs", 0.0) or 0.0)
    )
    return price_bps, cost_delta


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
    official_trades = [
        row for row in official.get("trades", []) if isinstance(row, dict)
    ]
    shadow_trades = [
        row
        for row in list(shadow.get("shadow_open_fills") or [])
        + list(shadow.get("shadow_exits") or [])
        if isinstance(row, dict)
    ]

    if shadow.get("shadow_evidence_complete") is False:
        payload = {
            "schema_version": 2,
            "status": "INCOMPLETE_EVIDENCE",
            "market_session": shadow.get("market_session"),
            "official_session": official.get("session"),
            "shadow_trade_count": len(shadow_trades),
            "official_trade_count": len(official_trades),
            "matched_trade_count": 0,
            "allowed_eod_only_trade_count": 0,
            "price_tolerance_bps": price_tolerance_bps,
            "cost_tolerance_usd": cost_tolerance_usd,
            "divergences": [],
            "divergence_counts": {},
            "explained_differences": [
                {
                    "kind": str(
                        shadow.get("shadow_evidence_incomplete_reason")
                        or "shadow_evidence_incomplete"
                    ),
                    "explanation": (
                        "The intraday shadow base lacks the T-1 pending-target contract, "
                        "so OPEN fills cannot be reconstructed safely. Official EOD remains "
                        "authoritative and no shadow/EOD equivalence claim is made."
                    ),
                }
            ],
            "reconciliation_skipped": True,
            "note": (
                "Incomplete rollout evidence is recorded explicitly; complete future "
                "shadow sessions remain fail-closed on material entry/rebalance divergence."
            ),
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return payload

    divergences: list[dict[str, Any]] = []
    explained_differences: list[dict[str, Any]] = []
    unmatched_official = set(range(len(official_trades)))
    matched = 0

    market_session = str(shadow.get("market_session") or "")
    official_session = str(official.get("session") or "")
    if market_session and official_session and market_session != official_session:
        divergences.append(
            {
                "kind": "session_mismatch",
                "shadow_session": market_session,
                "official_session": official_session,
            }
        )

    for row in shadow_trades:
        exact_index = next(
            (
                idx
                for idx in sorted(unmatched_official)
                if _key(official_trades[idx]) == _key(row)
            ),
            None,
        )
        peer_index = exact_index
        peer_match_kind = "exact"

        if peer_index is None and _is_exit(row):
            exit_candidates = [
                idx
                for idx in sorted(unmatched_official)
                if _same_trade_identity(row, official_trades[idx])
                and _is_exit(official_trades[idx])
            ]
            if len(exit_candidates) == 1:
                peer_index = exit_candidates[0]
                peer_match_kind = "exit_source_granularity"

        if peer_index is None:
            if _is_exit(row):
                explained_differences.append(
                    {
                        "kind": "intraday_only_exit_source_granularity",
                        "member": row.get("member"),
                        "symbol": row.get("symbol"),
                        "shadow_reason": row.get("reason"),
                        "shadow_fill_price": row.get("fill_price"),
                        "touched_at": row.get("touched_at"),
                        "explanation": (
                            "The point-in-time 5m shadow observed a stop/take touch that "
                            "does not have an equivalent official daily-OHLC trade. "
                            "Official EOD remains authoritative for portfolio accounting."
                        ),
                    }
                )
                continue
            divergences.append({"kind": "missing_official_trade", "shadow": row})
            continue

        peer = official_trades[peer_index]
        unmatched_official.discard(peer_index)
        matched += 1
        price_bps, cost_delta = _price_and_cost_delta(row, peer)

        if peer_match_kind == "exit_source_granularity":
            explained_differences.append(
                {
                    "kind": "intraday_path_vs_daily_ohlc_exit",
                    "member": row.get("member"),
                    "symbol": row.get("symbol"),
                    "shadow_reason": row.get("reason"),
                    "official_reason": peer.get("reason"),
                    "shadow_fill_price": row.get("fill_price"),
                    "official_fill_price": peer.get("fill_price"),
                    "price_bps": price_bps,
                    "cost_delta_usd": cost_delta,
                    "explanation": (
                        "5m point-in-time ordering and daily OHLC can resolve the same "
                        "stop/take exit differently. This difference is observational; "
                        "the official EOD trade owns portfolio accounting."
                    ),
                }
            )
            continue

        if price_bps > price_tolerance_bps or cost_delta > cost_tolerance_usd:
            if _is_exit(row) and _is_exit(peer):
                explained_differences.append(
                    {
                        "kind": "intraday_vs_daily_ohlc_exit_fill",
                        "member": row.get("member"),
                        "symbol": row.get("symbol"),
                        "shadow_reason": row.get("reason"),
                        "official_reason": peer.get("reason"),
                        "shadow_fill_price": row.get("fill_price"),
                        "official_fill_price": peer.get("fill_price"),
                        "price_bps": price_bps,
                        "cost_delta_usd": cost_delta,
                        "explanation": (
                            "The same exit exists in both paths but 5m and daily OHLC "
                            "produce different executable-path prices. Official EOD owns "
                            "the accounting value."
                        ),
                    }
                )
            else:
                divergences.append(
                    {
                        "kind": "trade_mismatch",
                        "key": _key(row),
                        "price_bps": price_bps,
                        "cost_delta_usd": cost_delta,
                        "shadow": row,
                        "official": peer,
                    }
                )

    allowed_eod_only = 0
    for idx in sorted(unmatched_official):
        row = official_trades[idx]
        if _is_eod_only_exit(row):
            allowed_eod_only += 1
            explained_differences.append(
                {
                    "kind": "eod_only_holding_exit",
                    "member": row.get("member"),
                    "symbol": row.get("symbol"),
                    "official_reason": row.get("reason"),
                    "official_fill_price": row.get("fill_price"),
                    "explanation": (
                        "Holding-session and session-timeout exits are intentionally "
                        "owned only by official EOD."
                    ),
                }
            )
        elif _is_exit(row):
            explained_differences.append(
                {
                    "kind": "daily_ohlc_only_exit_source_granularity",
                    "member": row.get("member"),
                    "symbol": row.get("symbol"),
                    "official_reason": row.get("reason"),
                    "official_fill_price": row.get("fill_price"),
                    "explanation": (
                        "Official daily OHLC observed a stop/take touch not present in "
                        "the point-in-time 5m shadow feed. Official EOD remains the "
                        "authoritative accounting path."
                    ),
                }
            )
        else:
            divergences.append(
                {"kind": "unexpected_official_only_trade", "official": row}
            )

    divergence_counts = dict(
        sorted(Counter(str(item.get("kind") or "unknown") for item in divergences).items())
    )
    explained_counts = dict(
        sorted(
            Counter(
                str(item.get("kind") or "unknown")
                for item in explained_differences
            ).items()
        )
    )
    payload = {
        "schema_version": 2,
        "status": "RECONCILED" if not divergences else "DIVERGED",
        "market_session": shadow.get("market_session"),
        "official_session": official.get("session"),
        "shadow_trade_count": len(shadow_trades),
        "official_trade_count": len(official_trades),
        "matched_trade_count": matched,
        "allowed_eod_only_trade_count": allowed_eod_only,
        "price_tolerance_bps": price_tolerance_bps,
        "cost_tolerance_usd": cost_tolerance_usd,
        "divergences": divergences,
        "divergence_counts": divergence_counts,
        "explained_differences": explained_differences,
        "explained_difference_counts": explained_counts,
        "note": (
            "Entry/rebalance equivalence remains fail-closed. Stop/take differences "
            "caused by 5m point-in-time versus daily-OHLC source granularity are "
            "audited explicitly while official EOD remains the accounting authority."
        ),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if divergences:
        raise RuntimeError(
            "Material shadow/EOD divergence: "
            f"{len(divergences)} counts={json.dumps(divergence_counts, sort_keys=True)}"
        )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fail-closed reconciliation between intraday shadow and official EOD"
    )
    parser.add_argument(
        "--shadow",
        default="data/metrics/strategy_league/live_snapshot.json",
    )
    parser.add_argument("--history", required=True)
    parser.add_argument(
        "--output",
        default="data/metrics/strategy_league/shadow_eod_reconciliation.json",
    )
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
