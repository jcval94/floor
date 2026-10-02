from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from math import tanh
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from contracts.model_contract import validate_model_artifact_contract
from contracts.trading import shadow_execution_contract
from league.capital_challenger import build_capital_challenger_targets
from league.engine import load_state
from models.train_weekly_opportunity import predict_weekly_opportunity
from strategies.breakout_protected_by_floor import generate_breakout_floor_orders
from strategies.cross_horizon_asymmetry import generate_cross_horizon_orders
from strategies.mean_reversion_floor_w1 import generate_mean_reversion_orders
from strategies.run_strategies import load_simple_yaml
from strategies.weekly_opportunity_ridge import generate_weekly_opportunity_orders

ET = ZoneInfo("America/New_York")
STRATEGY_IDS = (
    "weekly_opportunity_ridge",
    "breakout_protected_by_floor",
    "mean_reversion_floor_w1",
    "cross_horizon_asymmetry",
)


def _load_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Expected JSON object at {path}")
    return payload


def _merge_rows(market_rows: list[dict], forecast_rows: list[dict]) -> list[dict]:
    market_by_symbol = {
        str(row.get("symbol") or "").upper(): dict(row)
        for row in market_rows
    }
    forecast_by_symbol = {
        str(row.get("symbol") or "").upper(): dict(row)
        for row in forecast_rows
    }
    symbols = sorted(set(market_by_symbol) & set(forecast_by_symbol))
    return [
        {
            **market_by_symbol[symbol],
            **forecast_by_symbol[symbol],
            "symbol": symbol,
        }
        for symbol in symbols
    ]


def _overlay_checkpoint_quotes(
    rows: list[dict],
    checkpoint_quotes: dict[str, dict[str, Any]],
) -> tuple[list[dict], dict[str, Any]]:
    """Overlay point-in-time prices onto completed-daily features for shadow decisions.

    Forecasts remain trained/served from completed daily bars. Only the strategy
    observation layer receives the checkpoint price. Daily momentum and relative
    strength are algebraically updated from that price. Slower rolling features
    stay frozen at EOD. Fully completed 15m/1h returns are exposed to the shadow
    timing adapter; they never manufacture a BUY/SELL action on their own.
    """

    spy_quote = checkpoint_quotes.get("SPY", {})
    spy_ratio = 1.0
    spy_previous = float(spy_quote.get("previous_close", 0.0) or 0.0)
    spy_price = float(spy_quote.get("price", 0.0) or 0.0)
    if spy_previous > 0 and spy_price > 0:
        spy_ratio = spy_price / spy_previous

    overlaid: list[dict] = []
    fresh = 0
    for raw in rows:
        row = dict(raw)
        symbol = str(row.get("symbol") or "").upper()
        quote = checkpoint_quotes.get(symbol, {})
        live_price = float(quote.get("price", 0.0) or 0.0)
        base_close = float(row.get("close", 0.0) or 0.0)
        previous_close = float(quote.get("previous_close", 0.0) or 0.0)
        reference_close = previous_close if previous_close > 0 else base_close

        if live_price > 0 and reference_close > 0:
            fresh += 1
            price_ratio = live_price / reference_close
            base_m10 = float(row.get("momentum_10", 0.0) or 0.0)
            base_m20 = float(row.get("momentum_20", 0.0) or 0.0)
            base_rel20 = float(row.get("rel_strength_20", 0.0) or 0.0)
            base_spy_m20 = base_m20 - base_rel20

            live_m10 = (1.0 + base_m10) * price_ratio - 1.0
            live_m20 = (1.0 + base_m20) * price_ratio - 1.0
            live_spy_m20 = (1.0 + base_spy_m20) * spy_ratio - 1.0
            row["momentum_10"] = live_m10
            row["momentum_20"] = live_m20
            row["rel_strength_20"] = live_m20 - live_spy_m20

            row["close_eod_reference"] = base_close
            row["close"] = live_price
            row["intraday_price"] = live_price
            row["intraday_quote_as_of"] = quote.get("as_of")
            row["intraday_session_return"] = quote.get("session_return")
            row["intraday_return_15m"] = quote.get("return_15m")
            row["intraday_return_1h"] = quote.get("return_1h")
            row["intraday_session_high"] = quote.get("session_high")
            row["intraday_session_low"] = quote.get("session_low")
            row["intraday_completed_5m_bars"] = quote.get("completed_bars")
            row["intraday_quote_source"] = quote.get("source")
        else:
            row["close_eod_reference"] = base_close
            row["intraday_price"] = None
            row["intraday_quote_as_of"] = None
            row["intraday_session_return"] = None
            row["intraday_return_15m"] = None
            row["intraday_return_1h"] = None
            row["intraday_session_high"] = None
            row["intraday_session_low"] = None
            row["intraday_completed_5m_bars"] = 0
            row["intraday_quote_source"] = "eod_reference_fallback"

        overlaid.append(row)

    expected = len(rows)
    coverage = fresh / expected if expected else 1.0
    return overlaid, {
        "provider": "Yahoo Finance chart",
        "interval": "5m",
        "expected_symbols": expected,
        "fresh_symbols": fresh,
        "fresh_coverage": coverage,
        "spy_quote_available": spy_price > 0,
        "semantics": (
            "completed daily forecasts plus point-in-time 5m price overlay; "
            "15m/1h returns rank already-valid shadow actions but never create them"
        ),
    }


def _timing_context(
    strategy_id: str,
    decision: Any,
    row: dict[str, Any],
) -> dict[str, Any]:
    """Score point-in-time 15m/1h alignment without changing the source action.

    The adapter is intentionally ranking-only. It can strengthen or weaken the
    score used by the intraday Capital Challenger, but it cannot flip side,
    create quantity, or turn HOLD into BUY/SELL.
    """

    side = str(getattr(decision, "side", "HOLD") or "HOLD").upper()
    base_score = float(getattr(decision, "score", 0.0) or 0.0)
    r15_raw = row.get("intraday_return_15m")
    r1h_raw = row.get("intraday_return_1h")
    try:
        r15 = float(r15_raw) if r15_raw is not None else None
    except (TypeError, ValueError):
        r15 = None
    try:
        r1h = float(r1h_raw) if r1h_raw is not None else None
    except (TypeError, ValueError):
        r1h = None

    if side not in {"BUY", "SELL"}:
        return {
            "status": "not_applicable",
            "mode": "ranking_only",
            "base_score": base_score,
            "timing_score": 0.0,
            "multiplier": 1.0,
            "rank_score": base_score,
            "return_15m": r15,
            "return_1h": r1h,
            "action_changed": False,
            "qty_changed": False,
        }

    if r15 is None or r1h is None:
        return {
            "status": "unavailable",
            "mode": "ranking_only",
            "base_score": base_score,
            "timing_score": 0.0,
            "multiplier": 1.0,
            "rank_score": base_score,
            "return_15m": r15,
            "return_1h": r1h,
            "action_changed": False,
            "qty_changed": False,
        }

    direction = 1.0 if side == "BUY" else -1.0
    if strategy_id == "mean_reversion_floor_w1":
        # Mean reversion wants short-term stabilization/reversal in the trade
        # direction while the prior 1h move still reflects the dislocation.
        timing_score = (
            0.65 * tanh(direction * r15 / 0.003)
            + 0.35 * tanh(-direction * r1h / 0.008)
        )
        max_adjustment = 0.15
        timing_style = "reversal_confirmation"
    else:
        # Momentum/Cross-Horizon prefer immediate directional confirmation.
        # Weekly uses the same sign logic but with half the influence because
        # its alpha is deliberately slower-moving.
        timing_score = (
            0.60 * tanh(direction * r15 / 0.003)
            + 0.40 * tanh(direction * r1h / 0.008)
        )
        max_adjustment = 0.08 if strategy_id == "weekly_opportunity_ridge" else 0.15
        timing_style = (
            "directional_confirmation_mild"
            if strategy_id == "weekly_opportunity_ridge"
            else "directional_confirmation"
        )

    multiplier = 1.0 + max_adjustment * timing_score
    rank_score = max(0.0, base_score * multiplier)
    if timing_score >= 0.20:
        status = "confirmed"
    elif timing_score <= -0.20:
        status = "contradicted"
    else:
        status = "mixed"

    return {
        "status": status,
        "mode": "ranking_only",
        "style": timing_style,
        "base_score": base_score,
        "timing_score": timing_score,
        "multiplier": multiplier,
        "rank_score": rank_score,
        "return_15m": r15,
        "return_1h": r1h,
        "action_changed": False,
        "qty_changed": False,
    }


def _apply_intraday_timing(
    decisions_by_strategy: dict[str, list[Any]],
    rows_by_symbol: dict[str, dict[str, Any]],
) -> tuple[dict[str, list[Any]], dict[str, Any]]:
    """Return score-adjusted shadow copies plus a compact timing summary."""

    adjusted: dict[str, list[Any]] = {}
    status_counts = {
        "confirmed": 0,
        "mixed": 0,
        "contradicted": 0,
        "unavailable": 0,
        "not_applicable": 0,
    }
    actionable_seen = 0

    for strategy_id, decisions in decisions_by_strategy.items():
        strategy_rows: list[Any] = []
        for decision in decisions:
            symbol = str(getattr(decision, "symbol", "") or "")
            row = rows_by_symbol.get(symbol, {})
            timing = _timing_context(strategy_id, decision, row)
            status = str(timing.get("status") or "unavailable")
            status_counts[status] = status_counts.get(status, 0) + 1
            side = str(getattr(decision, "side", "HOLD") or "HOLD").upper()
            if side in {"BUY", "SELL"} and int(getattr(decision, "qty", 0) or 0) > 0:
                actionable_seen += 1

            trace = dict(getattr(decision, "decision_trace", None) or {})
            trace["intraday_timing"] = timing
            strategy_rows.append(
                replace(
                    decision,
                    score=float(timing["rank_score"]),
                    decision_trace=trace,
                )
            )
        adjusted[strategy_id] = strategy_rows

    return adjusted, {
        "mode": "ranking_only",
        "actionable_decisions_seen": actionable_seen,
        "status_counts": status_counts,
        "action_changes": 0,
        "qty_changes": 0,
        "max_score_adjustment_pct": {
            "weekly_opportunity_ridge": 0.08,
            "breakout_protected_by_floor": 0.15,
            "mean_reversion_floor_w1": 0.15,
            "cross_horizon_asymmetry": 0.15,
        },
    }


def _current_positions(data_dir: Path, league_id: str) -> dict[str, set[str]]:
    state = load_state(
        data_dir / "metrics" / "strategy_league" / "runs" / league_id
    )
    if not isinstance(state, dict):
        return {}
    members = state.get("members", {})
    if not isinstance(members, dict):
        return {}
    return {
        str(strategy_id): {
            str(symbol)
            for symbol, position in member.get("positions", {}).items()
            if isinstance(position, dict) and int(position.get("qty", 0) or 0) > 0
        }
        for strategy_id, member in members.items()
        if isinstance(member, dict)
    }


def _validate_weekly_artifact(
    weekly_artifact: dict,
    strategies_cfg: dict,
) -> None:
    validation = validate_model_artifact_contract(
        "weekly_opportunity",
        weekly_artifact,
        allow_legacy=True,
    )
    if not validation["valid"]:
        raise RuntimeError(
            "Intraday strategy observation rejected weekly model: "
            + ",".join(validation["errors"])
        )
    params = weekly_artifact.get("params", {})
    if params.get("canonical_serving_enabled") is not False:
        raise RuntimeError(
            "Intraday strategy observation requires the frozen non-canonical "
            "weekly challenger"
        )
    expected_semantics = (
        "net_directional_return_after_round_trip_costs_over_q1_downside"
    )
    strategy_semantics = str(
        strategies_cfg["strategies"]["weekly_opportunity_ridge"]
        .get("entry", {})
        .get("model_score_semantics")
        or ""
    )
    if strategy_semantics == "net_after_round_trip_costs":
        actual = str(params.get("target_semantics") or "")
        if actual != expected_semantics:
            raise RuntimeError(
                "Weekly intraday target semantics drift: "
                f"expected={expected_semantics} actual={actual!r}"
            )


def _serialize_decision(
    decision: Any,
    *,
    currently_held: bool,
    row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = row or {}
    trace = dict(decision.decision_trace or {})
    trace["intraday"] = {
        "price": row.get("intraday_price"),
        "quote_as_of": row.get("intraday_quote_as_of"),
        "session_return": row.get("intraday_session_return"),
        "return_15m": row.get("intraday_return_15m"),
        "return_1h": row.get("intraday_return_1h"),
        "session_high": row.get("intraday_session_high"),
        "session_low": row.get("intraday_session_low"),
        "completed_5m_bars": row.get("intraday_completed_5m_bars"),
        "quote_source": row.get("intraday_quote_source"),
        "eod_reference_close": row.get("close_eod_reference"),
    }
    return {
        "strategy_id": str(decision.strategy_id),
        "symbol": str(decision.symbol),
        "action": str(decision.side),
        "score": float(decision.score),
        "base_score": float(
            (trace.get("intraday_timing") or {}).get("base_score", decision.score)
        ),
        "intraday_rank_score": float(
            (trace.get("intraday_timing") or {}).get("rank_score", decision.score)
        ),
        "qty": int(decision.qty),
        "horizon": str(decision.horizon),
        "reason": str(decision.entry_reason),
        "stop_price": float(decision.stop_price or 0.0),
        "take_profit_price": float(decision.take_profit_price or 0.0),
        "expected_return": float(decision.expected_return or 0.0),
        "gross_alpha_pct": float(decision.gross_alpha_pct or 0.0),
        "net_alpha_pct": float(decision.net_alpha_pct or 0.0),
        "cost_pct": float(decision.cost_pct or 0.0),
        "alpha_source": str(decision.alpha_source or ""),
        "payoff_room_pct": float(decision.payoff_room_pct or 0.0),
        "m3_context": dict(decision.m3_context or {}),
        "decision_trace": trace,
        "currently_held": bool(currently_held),
    }


def _strategy_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    action_counts = {"BUY": 0, "SELL": 0, "HOLD": 0}
    for row in rows:
        action = str(row.get("action") or "HOLD").upper()
        action_counts[action] = action_counts.get(action, 0) + 1
    actionable = [
        row
        for row in rows
        if row.get("action") in {"BUY", "SELL"} and int(row.get("qty", 0) or 0) > 0
    ]
    actionable.sort(key=lambda row: float(row.get("score", 0.0)), reverse=True)
    return {
        "action_counts": action_counts,
        "actionable_count": len(actionable),
        "top_actionable": actionable[:10],
        "decisions": rows,
    }


def build_intraday_strategy_decisions(
    market_rows: list[dict],
    forecast_rows: list[dict],
    *,
    event_type: str,
    as_of: datetime,
    data_dir: Path,
    input_snapshot_id: str,
    strategies_config_path: Path | None = None,
    league_config_path: Path | None = None,
    weekly_artifact: dict | None = None,
    checkpoint_quotes: dict[str, dict[str, Any]] | None = None,
    quote_failures: list[str] | None = None,
) -> dict[str, Any]:
    """Evaluate every Strategy League family at every accepted market checkpoint.

    This is deliberately an observational shadow layer. It creates complete
    BUY/SELL/HOLD evidence from current checkpoint data but never routes an
    order. Official portfolio accounting remains owned by Strategy League EOD.
    """

    repo_root = Path(__file__).resolve().parents[2]
    strategies_config_path = strategies_config_path or repo_root / "config" / "strategies.yaml"
    league_config_path = league_config_path or repo_root / "config" / "strategy_league.json"

    strategies_cfg = deepcopy(load_simple_yaml(strategies_config_path))
    # Intraday observation must use the exact same friction contract as v11 EOD.
    strategies_cfg["costs"] = shadow_execution_contract()
    league_cfg = _load_json(league_config_path)

    if weekly_artifact is None:
        weekly_model_path = Path(str(league_cfg["weekly_model_path"]))
        if not weekly_model_path.is_absolute():
            weekly_model_path = repo_root / weekly_model_path
        weekly_artifact = _load_json(weekly_model_path)
    _validate_weekly_artifact(weekly_artifact, strategies_cfg)

    rows = _merge_rows(market_rows, forecast_rows)
    if len(rows) != len(forecast_rows):
        raise RuntimeError(
            "Intraday strategy observation refused incomplete market/forecast join: "
            f"market={len(market_rows)} forecasts={len(forecast_rows)} joined={len(rows)}"
        )

    checkpoint_quotes = checkpoint_quotes or {}
    quote_failures = quote_failures or []
    rows, quote_source = _overlay_checkpoint_quotes(rows, checkpoint_quotes)

    params = weekly_artifact.get("params", {})
    for row in rows:
        row["weekly_opportunity_score"] = predict_weekly_opportunity(row, params)

    league_id = str(league_cfg.get("league_id") or "")
    current_positions = _current_positions(data_dir, league_id)

    weekly = generate_weekly_opportunity_orders(
        rows,
        strategies_cfg,
        strategies_cfg["strategies"]["weekly_opportunity_ridge"],
        event_type,
        held_symbols=current_positions.get("weekly_opportunity_ridge", set()),
    )
    breakout = generate_breakout_floor_orders(
        rows,
        strategies_cfg,
        strategies_cfg["strategies"]["breakout_protected_by_floor"],
        event_type,
    )
    mean_reversion = generate_mean_reversion_orders(
        rows,
        strategies_cfg,
        strategies_cfg["strategies"]["mean_reversion_floor_w1"],
        event_type,
    )
    cross_horizon = generate_cross_horizon_orders(
        rows,
        strategies_cfg,
        strategies_cfg["strategies"]["cross_horizon_asymmetry"],
        event_type,
    )

    decisions_by_strategy = {
        "weekly_opportunity_ridge": weekly,
        "breakout_protected_by_floor": breakout,
        "mean_reversion_floor_w1": mean_reversion,
        "cross_horizon_asymmetry": cross_horizon,
    }
    rows_by_symbol = {str(row["symbol"]): row for row in rows}
    decisions_by_strategy, timing_summary = _apply_intraday_timing(
        decisions_by_strategy,
        rows_by_symbol,
    )
    challenger_targets = build_capital_challenger_targets(
        decisions_by_strategy,
        rows_by_symbol,
        strategies_cfg,
        dict(league_cfg.get("capital_allocation_challenger", {})),
    )

    strategy_payloads: dict[str, Any] = {}
    total_actionable = 0
    for strategy_id in STRATEGY_IDS:
        held = current_positions.get(strategy_id, set())
        serialized = [
            _serialize_decision(
                decision,
                currently_held=str(decision.symbol) in held,
                row=rows_by_symbol.get(str(decision.symbol)),
            )
            for decision in decisions_by_strategy[strategy_id]
        ]
        summary = _strategy_summary(serialized)
        total_actionable += int(summary["actionable_count"])
        strategy_payloads[strategy_id] = summary

    generated_at = as_of.astimezone(ET)
    return {
        "schema_version": 1,
        "artifact_type": "intraday_strategy_decisions",
        "league_id": league_id,
        "mode": "shadow_observation_no_execution",
        "event": str(event_type),
        "as_of": generated_at.isoformat(),
        "session_day": generated_at.date().isoformat(),
        "input_snapshot_id": input_snapshot_id,
        "strategy_evaluation_frequency": "every_accepted_market_checkpoint",
        "holding_horizon_is_not_entry_frequency": True,
        "official_portfolio_owner": "strategy_league_eod",
        "counts_as_promotion_evidence": False,
        "live_execution_enabled": False,
        "orders_emitted": False,
        "intraday_feature_caveat": (
            "Official forecasts use only completed daily bars. Shadow strategy "
            "decisions overlay the last fully completed 5m price at the accepted "
            "checkpoint. Fully completed 15m/1h returns adjust ranking only; they "
            "cannot change action, quantity, official portfolio state, or promotion."
        ),
        "intraday_timing_adapter": timing_summary,
        "quote_source": {
            **quote_source,
            "failed_symbols": sorted(set(quote_failures)),
        },
        "strategies": strategy_payloads,
        "capital_allocation_challenger": {
            "action": "ALLOCATE" if challenger_targets else "HOLD",
            "target_count": len(challenger_targets),
            "targets": challenger_targets,
            "reason": (
                "Eligible source BUY signals survived quality and portfolio-risk gates."
                if challenger_targets
                else "No eligible source BUY signal survived the source/quality/risk gates."
            ),
        },
        "summary": {
            "strategies_evaluated": len(strategy_payloads),
            "symbols_evaluated": len(rows),
            "decisions_evaluated": sum(
                len(item["decisions"]) for item in strategy_payloads.values()
            ),
            "actionable_decisions": total_actionable,
            "challenger_targets": len(challenger_targets),
            "quote_coverage": quote_source.get("fresh_coverage"),
        },
    }


def write_intraday_strategy_decisions(
    payload: dict[str, Any],
    *,
    data_dir: Path,
) -> Path:
    root = data_dir / "metrics" / "strategy_decisions" / "intraday"
    session_day = str(payload["session_day"])
    event = str(payload["event"])
    checkpoint = root / session_day / f"{event}.json"
    latest = root / "latest.json"
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    latest.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    checkpoint.write_text(serialized, encoding="utf-8")
    latest.write_text(serialized, encoding="utf-8")
    return checkpoint
