from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
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
) -> dict[str, Any]:
    return {
        "strategy_id": str(decision.strategy_id),
        "symbol": str(decision.symbol),
        "action": str(decision.side),
        "score": float(decision.score),
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
        "decision_trace": dict(decision.decision_trace or {}),
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
    # Intraday observation must use the exact same friction contract as v10 EOD.
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
            "Checkpoint rows may contain a partially formed current-session daily bar. "
            "Decisions are shadow evidence until intraday OOS behavior is validated."
        ),
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
