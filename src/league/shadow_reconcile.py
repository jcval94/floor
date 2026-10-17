from __future__ import annotations

import argparse
import copy
import json
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

from floor.calendar import previous_market_session
from league.engine import _execute_target
from storage.market_db import load_daily_bars
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


def _documented_open_quote_variance(
    shadow_trade: dict[str, Any],
    official_trade: dict[str, Any],
    shadow_snapshot: dict[str, Any],
    *,
    price_bps: float,
    cost_delta: float,
    base_price_tolerance_bps: float,
    max_source_variance_bps: float = 25.0,
    cost_tolerance_usd: float = 0.05,
) -> bool:
    """Bound a verified Yahoo 5m-vs-daily OPEN quote disagreement.

    Identity, size, costs, execution slippage and the source contract remain
    independently checked. Never extend the entry execution tolerance itself.
    """
    feed = shadow_snapshot.get("quote_source") or {}
    if (
        str(shadow_trade.get("shadow_event") or "") != "OPEN_FILL"
        or str(feed.get("provider") or "") != "Yahoo Finance chart"
        or str(feed.get("interval") or "") != "5m"
        or str(shadow_trade.get("reason") or "") not in {
            "signal_t_to_open_t_plus_1", "rebalance_at_next_open"
        }
        or not base_price_tolerance_bps < price_bps <= max_source_variance_bps
        or cost_delta > cost_tolerance_usd
    ):
        return False
    raw_shadow = float(shadow_trade.get("raw_price", 0.0) or 0.0)
    raw_official = float(official_trade.get("raw_price", 0.0) or 0.0)
    fill_shadow = float(shadow_trade.get("fill_price", 0.0) or 0.0)
    fill_official = float(official_trade.get("fill_price", 0.0) or 0.0)
    if min(raw_shadow, raw_official, fill_shadow, fill_official) <= 0:
        return False
    # Both simulated trades must apply the same execution contract to their
    # independently observed opening quotes (rounding tolerance 0.1 bps).
    return (
        abs(fill_shadow / raw_shadow - fill_official / raw_official) * 10000 < 0.1
        and abs(raw_shadow / raw_official - 1.0) * 10000 <= max_source_variance_bps
    )



def _verified_open_candle_envelope_variance(
    shadow_trade: dict[str, Any],
    official_trade: dict[str, Any],
    shadow_snapshot: dict[str, Any],
    *,
    price_bps: float,
    cost_delta: float,
    base_price_tolerance_bps: float,
    cost_tolerance_usd: float,
) -> dict[str, Any] | None:
    """Prove a >25bps discrepancy is a Yahoo 09:30 5m-vs-daily price-source difference.

    Both simulated executions must share a contract, the daily OPEN must lie
    inside the *observed* opening five-minute candle, and the discrepancy must
    remain under an independent 100bps risk ceiling. Never infer a candle from
    a missing or delayed quote; the official EOD price owns accounting.
    """
    if not 25.0 < price_bps <= 100.0:
        return None
    if not _documented_open_quote_variance(
        shadow_trade,
        official_trade,
        shadow_snapshot,
        price_bps=price_bps,
        cost_delta=cost_delta,
        base_price_tolerance_bps=base_price_tolerance_bps,
        max_source_variance_bps=100.0,
        cost_tolerance_usd=cost_tolerance_usd,
    ):
        return None
    bars = shadow_snapshot.get("open_quote_evidence")
    evidence = bars.get(str(shadow_trade.get("symbol") or "")) if isinstance(bars, dict) else None
    if not isinstance(evidence, dict):
        return None
    market_session = str(shadow_snapshot.get("market_session") or "")
    if not market_session or evidence.get("market_session") != market_session:
        return None
    if evidence.get("interval") != "5m":
        return None
    try:
        at = datetime.fromisoformat(str(evidence["timestamp"]).replace("Z", "+00:00"))
        if at.tzinfo is None:
            return None
        local = at.astimezone(ZoneInfo("America/New_York"))
        if local.date().isoformat() != market_session or (local.hour, local.minute) != (9, 30):
            return None
        candle_open = float(evidence["open"])
        high = float(evidence["high"])
        low = float(evidence["low"])
        candle_close = float(evidence["close"])
        shadow_raw = float(shadow_trade["raw_price"])
        official_raw = float(official_trade["raw_price"])
    except (TypeError, ValueError, KeyError):
        return None
    if not (0 < low <= min(candle_open, candle_close) <= max(candle_open, candle_close) <= high):
        return None
    # Rounding only (0.1bp), never permit an unproven price outside the candle.
    rounding = candle_open * 0.00001
    if abs(shadow_raw - candle_open) > rounding:
        return None
    if not low - rounding <= official_raw <= high + rounding:
        return None
    return {
        "market_session": market_session,
        "first_candle_at": evidence["timestamp"],
        "candle_open": candle_open,
        "candle_high": high,
        "candle_low": low,
        "candle_close": candle_close,
        "independent_risk_ceiling_bps": 100.0,
    }



def _verified_open_rounding_path_difference(
    row: dict[str, Any],
    official: dict[str, Any],
    shadow: dict[str, Any],
    *,
    base: dict[str, Any],
    market_db: Path | None,
    execution_cfg: dict[str, Any],
) -> dict[str, Any] | None:
    """Prove a one-share rounding difference by independently replaying OPEN twice.

    Source granularity can change floor(nav * weight / open) by exactly one
    share. Do not relax unmatched-trade rules unless each feed's complete
    OPEN execution ledger reproduces its actual observed ledger, the original
    authoritative T-1 hash matches, and official EOD records the held exit.
    """
    if (
        market_db is None
        or row.get("shadow_event") != "OPEN_FILL"
        or row.get("side") != "SELL"
        or row.get("reason") != "rebalance_at_next_open"
        or int(row.get("qty", 0)) != 1
        or not base
        or shadow.get("source_base_state_hash") != base.get("official_state_hash")
        or shadow.get("league_id") != base.get("league_id")
        or str(shadow.get("market_session") or "") != str(official.get("session") or "")
        or not str(base.get("official_state_hash") or "")
    ):
        return None
    session = str(shadow["market_session"])
    if str(base.get("last_eod_session") or "") != previous_market_session(
        datetime.fromisoformat(session).date()
    ).isoformat():
        return None
    member_id = str(row.get("member") or "")
    symbol = str(row.get("symbol") or "")
    member = (base.get("members") or {}).get(member_id)
    if not isinstance(member, dict) or not isinstance(member.get("pending_targets"), dict):
        return None
    initial_owned = int((member.get("positions") or {}).get(symbol, {}).get("qty", 0) or 0)
    if initial_owned < 2 or symbol not in member["pending_targets"]:
        return None
    # A matching EOD holding exit proves the divergent path was fully
    # accounted for by the official ledger, not an unrecorded real fill.
    exit_trades = [
        t for t in official.get("trades", [])
        if t.get("member") == member_id
        and t.get("symbol") == symbol
        and t.get("side") == "SELL"
        and (
            str(t.get("reason") or "").startswith("max_holding_sessions_")
            or t.get("reason") == "strategy_session_timeout"
        )
    ]
    if len(exit_trades) != 1:
        return None
    needed = set(member.get("positions") or {}) | set(member["pending_targets"])
    evidence = shadow.get("open_quote_evidence") or {}
    if not isinstance(evidence, dict) or any(
        not isinstance(evidence.get(sym), dict) for sym in needed
    ):
        return None
    daily_rows = load_daily_bars(market_db, sorted(needed))
    daily = {}
    for bar in daily_rows:
        date_at = datetime.fromisoformat(str(bar.get("timestamp") or "").replace("Z", "+00:00"))
        if date_at.astimezone(ZoneInfo("America/New_York")).date().isoformat() == session:
            daily[str(bar["symbol"])] = bar
    if set(daily) != needed:
        return None
    five_min = {}
    for sym in needed:
        proof = evidence[sym]
        try:
            at = datetime.fromisoformat(str(proof["timestamp"]).replace("Z", "+00:00"))
            raw = float(proof["open"])
            high = float(proof["high"])
            low = float(proof["low"])
            close = float(proof["close"])
            official_open = float(daily[sym]["open"])
        except (KeyError, TypeError, ValueError):
            return None
        local = at.astimezone(ZoneInfo("America/New_York"))
        if (
            proof.get("market_session") != session
            or proof.get("interval") != "5m"
            or local.date().isoformat() != session
            or (local.hour, local.minute) != (9, 30)
            or not (0 < low <= min(raw, close) <= max(raw, close) <= high)
            or not low <= official_open <= high
        ):
            return None
        five_min[sym] = {"open": raw}

    def simulate(bars: dict[str, dict]) -> list[dict[str, Any]]:
        clone = copy.deepcopy(member)
        clone["id"] = member_id
        trades: list[dict[str, Any]] = []
        _execute_target(
            clone, clone["pending_targets"], bars, execution_cfg, trades,
            session=session,
            session_number=int(base.get("sessions", 0) or 0) + 1,
        )
        return trades

    def signatures(trades: list[dict[str, Any]]) -> Counter:
        return Counter((
            str(t.get("member") or ""),
            str(t.get("symbol") or ""),
            str(t.get("side") or ""),
            int(t.get("qty", 0) or 0),
            str(t.get("reason") or ""),
            round(float(t.get("raw_price", 0.0) or 0.0), 5),
            round(float(t.get("fill_price", 0.0) or 0.0), 5),
            round(float(t.get("costs", 0.0) or 0.0), 5),
        ) for t in trades)

    observed_shadow_open = [
        t for t in shadow.get("shadow_open_fills", [])
        if t.get("member") == member_id
    ]
    observed_official_open = [
        t for t in official.get("trades", [])
        if t.get("member") == member_id and t.get("reason") in {
            "rebalance_at_next_open", "signal_t_to_open_t_plus_1"
        }
    ]
    expected_shadow = simulate(five_min)
    expected_official = simulate(daily)
    if (
        signatures(expected_shadow) != signatures(observed_shadow_open)
        or signatures(expected_official) != signatures(observed_official_open)
        or signatures([row]) != signatures([
            t for t in expected_shadow
            if t.get("member") == member_id and t.get("symbol") == symbol
            and t.get("side") == "SELL"
            and t.get("reason") == "rebalance_at_next_open"
        ])
        or any(
            t.get("symbol") == symbol and t.get("side") == "SELL"
            and t.get("reason") == "rebalance_at_next_open"
            for t in expected_official
        )
    ):
        return None
    price_bps = abs(float(five_min[symbol]["open"]) / float(daily[symbol]["open"]) - 1) * 10000
    if not 0 < price_bps <= 100:
        return None
    return {
        "kind": "verified_one_share_open_rebalance_quantization",
        "member": member_id,
        "symbol": symbol,
        "side": "SELL",
        "shadow_qty": 1,
        "original_owned_qty": initial_owned,
        "official_open": daily[symbol]["open"],
        "shadow_open": five_min[symbol]["open"],
        "opening_price_difference_bps": price_bps,
        "shadow_open_replay_verified": True,
        "official_open_replay_verified": True,
        "official_same_symbol_holding_exit": exit_trades[0],
        "source_state_hash": str(base["official_state_hash"]),
        "explanation": (
            "Different independently observed Yahoo daily/5m opening prices moved "
            "the integer share-sizing threshold by exactly one. Replay of the "
            "full member OPEN execution under both feeds matches both ledgers. "
            "The official held position exits at EOD; no shadow fill is "
            "promoted to official accounting."
        ),
    }



def reconcile(
    *,
    shadow_path: Path,
    history_path: Path,
    output_path: Path,
    price_tolerance_bps: float = 8.0,
    cost_tolerance_usd: float = 0.05,
    base_path: Path | None = None,
    market_db: Path | None = None,
    league_config_path: Path | None = None,
) -> dict[str, Any]:
    shadow = _load(shadow_path)
    official = _latest_history(history_path)
    baseline = _load(base_path) if base_path is not None else {}
    execution_cfg = _load(league_config_path).get("execution", {}) if league_config_path else {}
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
        payload: dict[str, Any] = {
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
            verified = _verified_open_rounding_path_difference(
                row, official, shadow,
                base=baseline, market_db=market_db, execution_cfg=execution_cfg,
            )
            if verified is not None:
                explained_differences.append(verified)
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
            elif _documented_open_quote_variance(
                row,
                peer,
                shadow,
                price_bps=price_bps,
                cost_delta=cost_delta,
                base_price_tolerance_bps=price_tolerance_bps,
                cost_tolerance_usd=cost_tolerance_usd,
            ):
                explained_differences.append(
                    {
                        "kind": "verified_5m_vs_daily_open_source_variance",
                        "member": row.get("member"),
                        "symbol": row.get("symbol"),
                        "side": row.get("side"),
                        "qty": row.get("qty"),
                        "shadow_raw_price": row.get("raw_price"),
                        "official_raw_price": peer.get("raw_price"),
                        "shadow_fill_price": row.get("fill_price"),
                        "official_fill_price": peer.get("fill_price"),
                        "price_bps": price_bps,
                        "cost_delta_usd": cost_delta,
                        "max_source_variance_bps": 25.0,
                        "explanation": (
                            "Matched OPEN trade and execution contract from independently "
                            "observed Yahoo 5m vs daily bar quotes. The official daily-open "
                            "fill owns accounting; the intraday quote variance remains audited."
                        ),
                    }
                )
            elif (opening_proof := _verified_open_candle_envelope_variance(
                row,
                peer,
                shadow,
                price_bps=price_bps,
                cost_delta=cost_delta,
                base_price_tolerance_bps=price_tolerance_bps,
                cost_tolerance_usd=cost_tolerance_usd,
            )) is not None:
                explained_differences.append(
                    {
                        "kind": "verified_5m_open_candle_daily_open_envelope",
                        "member": row.get("member"),
                        "symbol": row.get("symbol"),
                        "side": row.get("side"),
                        "qty": row.get("qty"),
                        "shadow_raw_price": row.get("raw_price"),
                        "official_raw_price": peer.get("raw_price"),
                        "shadow_fill_price": row.get("fill_price"),
                        "official_fill_price": peer.get("fill_price"),
                        "price_bps": price_bps,
                        "cost_delta_usd": cost_delta,
                        "opening_bar_provenance": opening_proof,
                        "explanation": (
                            "Same OPEN execution identity and cost contract; the daily "
                            "OPEN is contained in the observed 09:30 ET five-minute OHLC "
                            "envelope. Official daily data owns accounting; the distinct "
                            "intraday opening quote is retained for audit."
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
    parser.add_argument("--base", default=None)
    parser.add_argument("--market-db", default=None)
    parser.add_argument("--league-config", default=None)
    args = parser.parse_args()
    payload = reconcile(
        shadow_path=Path(args.shadow),
        history_path=Path(args.history),
        output_path=Path(args.output),
        price_tolerance_bps=args.price_tolerance_bps,
        cost_tolerance_usd=args.cost_tolerance_usd,
        base_path=Path(args.base) if args.base else None,
        market_db=Path(args.market_db) if args.market_db else None,
        league_config_path=Path(args.league_config) if args.league_config else None,
    )
    print(json.dumps(payload, sort_keys=True))


if __name__ == "__main__":
    main()
