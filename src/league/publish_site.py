from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from utils.market_session import ET, checkpoint_times, session_info_for_day


BENCHMARK_IDS = {"benchmark_spy", "benchmark_equal_weight"}
CHALLENGER_ID = "capital_allocation_challenger"
DEFAULT_LEAGUE_ID = "strategy_league_v11_intraday_informed_10k"

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
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

def _number(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric

def _return_sort_key(row: dict[str, Any]) -> tuple[bool, float]:
    value = _number(row.get("return"))
    return (
        value is not None,
        value if value is not None else float("-inf"),
    )

def _rank_rows(raw_rows: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_rows, list):
        return []
    rows = [dict(row) for row in raw_rows if isinstance(row, dict)]
    rows.sort(key=_return_sort_key, reverse=True)
    previous_return: float | None = None
    previous_rank = 0
    for index, row in enumerate(rows, start=1):
        current_return = _return_of(row)
        if index == 1 or current_return is None or previous_return is None or abs(current_return - previous_return) > 1e-12:
            previous_rank = index
        row["rank"] = previous_rank
        previous_return = current_return
        strategy_id = str(row.get("strategy") or "")
        row["member_type"] = (
            "benchmark" if strategy_id in BENCHMARK_IDS else "strategy"
        )
    return rows

def _row_by_id(rows: list[dict[str, Any]], strategy_id: str) -> dict[str, Any] | None:
    return next(
        (row for row in rows if str(row.get("strategy")) == strategy_id),
        None,
    )

def _return_of(row: dict[str, Any] | None) -> float | None:
    return _number(row.get("return")) if row else None

def _delta(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return left - right

def _competition_summary(rows: list[dict[str, Any]], sessions: int, min_sessions: int) -> dict[str, Any]:
    strategy_rows = [row for row in rows if row.get("member_type") == "strategy"]
    base_rows = [
        row
        for row in strategy_rows
        if str(row.get("strategy")) != CHALLENGER_ID
    ]

    overall_leader = rows[0] if len(rows) > 1 and _return_of(rows[0]) != _return_of(rows[1]) and sessions >= min_sessions else None
    strategy_leader = strategy_rows[0] if len(strategy_rows) > 1 and _return_of(strategy_rows[0]) != _return_of(strategy_rows[1]) and sessions >= min_sessions else None
    challenger = _row_by_id(rows, CHALLENGER_ID)
    spy = _row_by_id(rows, "benchmark_spy")
    best_base = base_rows[0] if base_rows else None

    challenger_return = _return_of(challenger)
    return {
        "leader_status": "PROVISIONAL" if strategy_leader or overall_leader else "INSUFFICIENT_EVIDENCE",
        "min_sessions_for_leader": min_sessions,
        "sessions": sessions,
        "overall_leader": overall_leader.get("strategy") if overall_leader else None,
        "overall_leader_return": _return_of(overall_leader),
        "strategy_leader": strategy_leader.get("strategy") if strategy_leader else None,
        "strategy_leader_return": _return_of(strategy_leader),
        "challenger_rank": challenger.get("rank") if challenger else None,
        "challenger_return": challenger_return,
        "challenger_vs_spy": _delta(challenger_return, _return_of(spy)),
        "best_base_strategy": best_base.get("strategy") if best_base else None,
        "challenger_vs_best_base": _delta(
            challenger_return,
            _return_of(best_base),
        ),
        "members": len(rows),
        "strategies": len(strategy_rows),
        "benchmarks": len(rows) - len(strategy_rows),
    }

def _member_ids(league_cfg: dict[str, Any]) -> list[str]:
    raw = league_cfg.get("members", [])
    if not isinstance(raw, list):
        return []
    return [
        str(member.get("id"))
        for member in raw
        if isinstance(member, dict) and member.get("id")
    ]

def _configured_data_path(data_dir: Path, raw_path: Any) -> Path | None:
    raw = str(raw_path or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if path.is_absolute():
        return path
    if path.parts and path.parts[0] == "data":
        return data_dir.joinpath(*path.parts[1:])
    return data_dir / path

def _weekly_model_summary(data_dir: Path, league_cfg: dict[str, Any]) -> dict[str, Any]:
    model_path = _configured_data_path(data_dir, league_cfg.get("weekly_model_path"))
    payload = _load_object(model_path)
    metrics = payload.get("metrics", {}) if isinstance(payload.get("metrics"), dict) else {}
    correlation = _number(metrics.get("spearman_rank_correlation"))
    lift = _number(
        metrics.get("top_quintile_net_return_lift")
        if metrics.get("top_quintile_net_return_lift") is not None
        else metrics.get("top_quintile_return_lift")
    )
    validation_warning = bool(payload) and (
        (correlation is not None and correlation <= 0)
        or (lift is not None and lift <= 0)
    )
    return {
        "status": "FROZEN" if payload else "MISSING",
        "model_name": payload.get("model_name") if payload else None,
        "version": payload.get("version") if payload else None,
        "trained_at": (payload.get("trained_at") or payload.get("as_of")) if payload else None,
        "validation_metrics": metrics,
        "validation_warning": validation_warning,
    }

def _waiting_payload(
    league_cfg: dict[str, Any],
    *,
    status: str,
    detail: str,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "league_id": str(league_cfg.get("league_id") or DEFAULT_LEAGUE_ID),
        "mode": "shadow_paper",
        "status": status,
        "detail": detail,
        "start_session": None,
        "last_session": None,
        "sessions": 0,
        "initial_nav_usd": float(league_cfg.get("initial_nav_usd", 10000.0)),
        "scheduled_members": _member_ids(league_cfg),
        "automatic_promotion": False,
        "live_execution_enabled": False,
        "rows": [],
    }

def publish_league_payload(
    data_dir: Path,
    output_path: Path,
    league_config_path: Path | None = None,
) -> dict[str, Any]:
    source = data_dir / "metrics" / "strategy_league" / "leaderboard.json"
    source_payload = _load_object(source)
    league_cfg = _load_object(league_config_path)
    expected_league_id = str(league_cfg.get("league_id") or DEFAULT_LEAGUE_ID)
    weekly_model = _weekly_model_summary(data_dir, league_cfg)
    weekly_ready = weekly_model.get("status") == "FROZEN"
    weekly_path_configured = bool(str(league_cfg.get("weekly_model_path") or "").strip())
    weekly_missing = not weekly_ready and (weekly_path_configured or not league_cfg)
    waiting_status = "WAITING_FOR_WEEKLY_MODEL" if weekly_missing else "WAITING_FOR_GENESIS"

    if not source_payload:
        payload = _waiting_payload(
            league_cfg,
            status=waiting_status,
            detail=(
                "Frozen Weekly challenger is ready; waiting for the first complete EOD "
                "to create clean prospective league genesis."
                if weekly_ready
                else "Frozen Weekly challenger is missing; bootstrap must complete before league genesis."
            ),
        )
    elif league_cfg and str(source_payload.get("league_id") or "") != expected_league_id:
        previous_id = str(source_payload.get("league_id") or "unknown")
        payload = _waiting_payload(
            league_cfg,
            status=waiting_status,
            detail=(
                (
                    f"Current runtime evidence belongs to previous league {previous_id}; "
                    f"frozen Weekly challenger is ready and the system is waiting for the first "
                    f"complete EOD of {expected_league_id}."
                )
                if weekly_ready
                else (
                    f"Current runtime evidence belongs to previous league {previous_id}; "
                    "the frozen Weekly challenger for the current league is missing."
                )
            ),
        )
    else:
        payload = dict(source_payload)

    rows = _rank_rows(payload.get("rows", []))
    payload["rows"] = rows
    min_sessions = max(2, int(league_cfg.get("weekly_review_frequency_sessions", 5)))
    payload["summary"] = _competition_summary(rows, int(payload.get("sessions") or 0), min_sessions)
    payload["weekly_model"] = weekly_model
    payload["evidence_type"] = "prospective_shadow_paper"
    payload["published_at"] = datetime.now(timezone.utc).isoformat()
    payload["automatic_promotion"] = False
    payload["live_execution_enabled"] = False
    _write_object(output_path, payload)
    return payload


def publish_live_payload(
    data_dir: Path,
    output_path: Path,
    league_config_path: Path | None = None,
) -> dict[str, Any]:
    """Publish the latest observational intraday mark-to-market snapshot.

    Intraday rows are deliberately isolated from official EOD evidence. A snapshot
    based on an older EOD state is withheld rather than mixed with the current
    prospective Strategy League epoch.
    """

    source = data_dir / "metrics" / "strategy_league" / "live_snapshot.json"
    source_payload = _load_object(source)
    league_cfg = _load_object(league_config_path)
    expected_league_id = str(league_cfg.get("league_id") or DEFAULT_LEAGUE_ID)
    official = _load_object(
        data_dir / "metrics" / "strategy_league" / "leaderboard.json"
    )

    status = "WAITING_FOR_LIVE_SNAPSHOT"
    detail = "The first market-hours intraday snapshot has not been published yet."
    payload: dict[str, Any]
    if not source_payload:
        payload = {
            "schema_version": 1,
            "league_id": expected_league_id,
            "mode": "shadow_paper_mark_to_market",
            "status": status,
            "detail": detail,
            "market_session": None,
            "generated_at": None,
            "last_eod_session": official.get("last_session"),
            "sessions": int(official.get("sessions", 0) or 0),
            "initial_nav_usd": float(league_cfg.get("initial_nav_usd", 10000.0)),
            "quote_source": {
                "provider": "Yahoo Finance chart",
                "interval": "5m",
                "required_symbols": 0,
                "fresh_symbols": 0,
                "fresh_coverage": 0.0,
            },
            "rows": [],
        }
    elif str(source_payload.get("league_id") or "") != expected_league_id:
        payload = {
            "schema_version": 1,
            "league_id": expected_league_id,
            "mode": "shadow_paper_mark_to_market",
            "status": "WAITING_FOR_LIVE_SNAPSHOT",
            "detail": (
                "The available intraday snapshot belongs to a previous Strategy "
                "League epoch and is not published."
            ),
            "market_session": None,
            "generated_at": None,
            "last_eod_session": official.get("last_session"),
            "sessions": int(official.get("sessions", 0) or 0),
            "initial_nav_usd": float(league_cfg.get("initial_nav_usd", 10000.0)),
            "quote_source": {
                "provider": "Yahoo Finance chart",
                "interval": "5m",
                "required_symbols": 0,
                "fresh_symbols": 0,
                "fresh_coverage": 0.0,
            },
            "rows": [],
        }
    elif (
        official
        and official.get("last_session")
        and source_payload.get("last_eod_session")
        != official.get("last_session")
    ):
        payload = {
            "schema_version": 1,
            "league_id": expected_league_id,
            "mode": "shadow_paper_mark_to_market",
            "status": "STALE_BASE",
            "detail": (
                "The latest intraday snapshot predates the current official EOD "
                "Strategy League state, so its rows are withheld."
            ),
            "market_session": source_payload.get("market_session"),
            "generated_at": source_payload.get("generated_at"),
            "last_eod_session": source_payload.get("last_eod_session"),
            "sessions": int(official.get("sessions", 0) or 0),
            "initial_nav_usd": float(league_cfg.get("initial_nav_usd", 10000.0)),
            "quote_source": source_payload.get("quote_source", {}),
            "rows": [],
        }
    else:
        payload = dict(source_payload)
        payload.pop("quote_cache", None)
        payload["rows"] = _rank_rows(payload.get("rows", []))

    payload["evidence_type"] = "intraday_mark_to_market_non_promotional"
    payload["counts_as_prospective_evidence"] = False
    payload["automatic_promotion"] = False
    payload["live_execution_enabled"] = False
    payload["published_at"] = datetime.now(timezone.utc).isoformat()
    _write_object(output_path, payload)
    return payload

def _intraday_operational_timeline(
    data_dir: Path,
    *,
    session_day: str,
    checkpoints: list[dict[str, Any]],
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Describe checkpoint execution separately from trading decisions.

    A completed checkpoint may be ON_TIME or CHECKPOINT_CATCH_UP. Missing
    checkpoints become SCHEDULER_MISSED only after the same 180-minute recovery
    budget used by the intraday workflow. Decision state is independent:
    ACTIONABLE means BUY/SELL candidates existed; HOLD means the engine evaluated
    normally and intentionally produced no actionable candidates.
    """

    if not session_day:
        return []
    try:
        day = date.fromisoformat(session_day)
    except ValueError:
        return []

    info = session_info_for_day(day)
    expected = checkpoint_times(info)
    now_et = (now or datetime.now(tz=ET)).astimezone(ET)
    decision_by_event = {
        str(item.get("event") or ""): item
        for item in checkpoints
        if isinstance(item, dict)
    }
    marker_dir = data_dir / "snapshots" / "workflow_runs"
    rows: list[dict[str, Any]] = []

    for event in ("OPEN", "OPEN_PLUS_2H", "OPEN_PLUS_4H", "OPEN_PLUS_6H", "CLOSE"):
        checkpoint_at = expected.get(event)
        if checkpoint_at is None:
            continue
        marker = _load_object(marker_dir / f"intraday_{session_day}_{event}.json")
        missing = _load_object(
            marker_dir / f"intraday_missing_{session_day}_{event}.json"
        )
        decision = decision_by_event.get(event, {})
        summary = decision.get("summary", {}) if isinstance(decision, dict) else {}
        actionable = int(summary.get("actionable_decisions", 0) or 0)
        evaluations = int(summary.get("decisions_evaluated", 0) or 0)
        decision_state = (
            "ACTIONABLE"
            if actionable > 0
            else "HOLD"
            if evaluations > 0
            else "NO_DECISION"
        )

        guard_reason = str(marker.get("guard_reason") or "")
        try:
            lateness = int(str(marker.get("lateness_minutes") or "0") or 0)
        except ValueError:
            lateness = 0
        completed_at = marker.get("completed_at")
        inferred_late = False
        if completed_at:
            try:
                completed_dt = datetime.fromisoformat(
                    str(completed_at).replace("Z", "+00:00")
                )
                if completed_dt.tzinfo is None:
                    completed_dt = completed_dt.replace(tzinfo=ET)
                inferred_late = (
                    completed_dt.astimezone(ET) - checkpoint_at
                ) > timedelta(minutes=45)
            except ValueError:
                inferred_late = False

        if marker:
            catch_up = (
                guard_reason
                in {
                    "checkpoint_catchup",
                    "repair_existing_checkpoint",
                    "manual_repair",
                }
                or lateness >= 30
                or inferred_late
            )
            operational_state = (
                "CHECKPOINT_CATCH_UP" if catch_up else "ON_TIME"
            )
            detail = (
                f"Recovered checkpoint; guard_reason={guard_reason or 'legacy'} "
                f"lateness_minutes={lateness}"
                if catch_up
                else "Checkpoint completed within the normal scheduler window."
            )
        elif missing:
            operational_state = "SCHEDULER_MISSED"
            detail = (
                "Checkpoint could not be reconstructed: "
                + str(missing.get("reason") or "missing runtime evidence")
            )
        elif now_et >= checkpoint_at + timedelta(minutes=180):
            operational_state = "SCHEDULER_MISSED"
            detail = "No completed checkpoint marker exists after the 180-minute recovery budget."
        else:
            operational_state = "PENDING"
            detail = (
                "Checkpoint is due and still inside the recovery budget."
                if now_et >= checkpoint_at
                else "Checkpoint is not due yet."
            )

        rows.append(
            {
                "event": event,
                "checkpoint_at": checkpoint_at.isoformat(),
                "operational_state": operational_state,
                "decision_state": decision_state,
                "guard_reason": guard_reason or None,
                "lateness_minutes": lateness if marker else None,
                "completed_at": completed_at,
                "missing_reason": missing.get("reason") if missing else None,
                "detail": detail,
                "summary": summary,
                "capital_allocation_challenger": (
                    decision.get("capital_allocation_challenger", {})
                    if isinstance(decision, dict)
                    else {}
                ),
                "as_of": decision.get("as_of") if isinstance(decision, dict) else None,
            }
        )
    return rows


def publish_intraday_decision_payload(
    data_dir: Path,
    output_path: Path,
    league_config_path: Path | None = None,
) -> dict[str, Any]:
    """Publish the latest checkpoint-level strategy decisions safely."""

    source = data_dir / "metrics" / "strategy_decisions" / "intraday" / "latest.json"
    source_payload = _load_object(source)
    league_cfg = _load_object(league_config_path)
    expected_league_id = str(league_cfg.get("league_id") or DEFAULT_LEAGUE_ID)

    if not source_payload:
        payload: dict[str, Any] = {
            "schema_version": 1,
            "artifact_type": "intraday_strategy_decisions",
            "league_id": expected_league_id,
            "mode": "shadow_observation_no_execution",
            "status": "WAITING_FOR_INTRADAY_DECISIONS",
            "detail": "No checkpoint-level strategy decision snapshot is available yet.",
            "event": None,
            "as_of": None,
            "session_day": None,
            "strategies": {},
            "capital_allocation_challenger": {
                "action": "HOLD",
                "target_count": 0,
                "targets": {},
                "reason": "Waiting for the first accepted market checkpoint.",
            },
            "summary": {
                "strategies_evaluated": 0,
                "symbols_evaluated": 0,
                "decisions_evaluated": 0,
                "actionable_decisions": 0,
                "challenger_targets": 0,
            },
        }
    elif str(source_payload.get("league_id") or "") != expected_league_id:
        payload = {
            "schema_version": 1,
            "artifact_type": "intraday_strategy_decisions",
            "league_id": expected_league_id,
            "mode": "shadow_observation_no_execution",
            "status": "WAITING_FOR_INTRADAY_DECISIONS",
            "detail": (
                "The available checkpoint decisions belong to a previous "
                "Strategy League epoch and are withheld."
            ),
            "event": None,
            "as_of": None,
            "session_day": None,
            "strategies": {},
            "capital_allocation_challenger": {
                "action": "HOLD",
                "target_count": 0,
                "targets": {},
                "reason": "Waiting for checkpoint decisions from the current league.",
            },
            "summary": {
                "strategies_evaluated": 0,
                "symbols_evaluated": 0,
                "decisions_evaluated": 0,
                "actionable_decisions": 0,
                "challenger_targets": 0,
            },
        }
    else:
        payload = dict(source_payload)
        payload["status"] = "READY"

    event_order = ["OPEN", "OPEN_PLUS_2H", "OPEN_PLUS_4H", "OPEN_PLUS_6H", "CLOSE"]
    session_day = str(payload.get("session_day") or "")
    checkpoints: list[dict[str, Any]] = []
    root = data_dir / "metrics" / "strategy_decisions" / "intraday" / session_day
    if session_day and root.exists():
        for event in event_order:
            item = _load_object(root / f"{event}.json")
            if item and str(item.get("league_id") or "") == expected_league_id:
                checkpoints.append(item)
    payload["checkpoints"] = checkpoints
    operational_timeline = _intraday_operational_timeline(
        data_dir,
        session_day=session_day,
        checkpoints=checkpoints,
    )
    payload["operational_timeline"] = operational_timeline

    live_snapshot = _load_object(
        data_dir / "metrics" / "strategy_league" / "live_snapshot.json"
    )
    payload["observations_30m"] = list(live_snapshot.get("observations") or [])
    payload["shadow_portfolio"] = {
        "status": live_snapshot.get("status"),
        "market_session": live_snapshot.get("market_session"),
        "generated_at": live_snapshot.get("generated_at"),
        "fills": list(live_snapshot.get("shadow_open_fills") or []),
        "exits": list(live_snapshot.get("shadow_exits") or []),
        "rows": list(live_snapshot.get("rows") or []),
    }

    latest_summary = payload.get("summary") or {}
    latest_challenger = payload.get("capital_allocation_challenger") or {}
    latest_actionable = int(latest_summary.get("actionable_decisions", 0) or 0)
    latest_evaluations = int(latest_summary.get("decisions_evaluated", 0) or 0)
    latest_decision_state = (
        "ACTIONABLE"
        if latest_actionable > 0
        else "HOLD"
        if latest_evaluations > 0
        else "WAITING"
    )
    latest_event = str(payload.get("event") or "")
    latest_checkpoint = next(
        (
            row
            for row in operational_timeline
            if str(row.get("event") or "") == latest_event
        ),
        None,
    )
    missed_count = sum(
        1
        for row in operational_timeline
        if row.get("operational_state") == "SCHEDULER_MISSED"
    )
    catchup_count = sum(
        1
        for row in operational_timeline
        if row.get("operational_state") == "CHECKPOINT_CATCH_UP"
    )
    payload["operational_state"] = {
        "decision_state": latest_decision_state,
        "latest_event": latest_event or None,
        "latest_event_kind": (
            "HEARTBEAT"
            if latest_event.startswith("HEARTBEAT_")
            else "OFFICIAL_CHECKPOINT"
            if latest_event
            else "WAITING"
        ),
        "checkpoint_state": (
            latest_checkpoint.get("operational_state")
            if latest_checkpoint
            else "HEARTBEAT"
            if latest_event.startswith("HEARTBEAT_")
            else "WAITING"
        ),
        "scheduler_state": "MISSED" if missed_count else "HEALTHY",
        "scheduler_missed_checkpoints": missed_count,
        "checkpoint_catchups": catchup_count,
        "actionable_decisions": latest_actionable,
        "decisions_evaluated": latest_evaluations,
    }
    fills = list(live_snapshot.get("shadow_open_fills") or [])
    exits = list(live_snapshot.get("shadow_exits") or [])
    live_rows = list(live_snapshot.get("rows") or [])
    positions = sum(len(row.get("positions") or []) for row in live_rows if isinstance(row, dict))
    evaluations = int(latest_summary.get("decisions_evaluated", 0) or 0)
    actionable = int(latest_summary.get("actionable_decisions", 0) or 0)
    targets = int(latest_challenger.get("target_count", 0) or 0)
    gross_pnl = sum(float(row.get("gross_pnl", 0.0) or 0.0) for row in live_rows if isinstance(row, dict))
    costs = sum(float(row.get("costs", 0.0) or 0.0) for row in live_rows if isinstance(row, dict))
    net_pnl = sum(float(row.get("net_pnl", 0.0) or 0.0) for row in live_rows if isinstance(row, dict))
    gross_notional = sum(float(row.get("notional", 0.0) or 0.0) for row in fills + exits if isinstance(row, dict))
    nav_denominator = sum(float(row.get("eod_nav", 0.0) or 0.0) for row in live_rows if isinstance(row, dict))
    payload["funnel"] = {
        "evaluations": evaluations,
        "actionable": actionable,
        "challenger_targets": targets,
        "fills": len(fills),
        "positions": positions,
        "exits": len(exits),
        "net_pnl": net_pnl,
    }
    payload["session_metrics"] = {
        "actionable_rate": actionable / evaluations if evaluations else 0.0,
        "target_rate": targets / actionable if actionable else 0.0,
        "execution_rate": len(fills) / targets if targets else 0.0,
        "turnover": gross_notional / nav_denominator if nav_denominator else 0.0,
        "cost_drag": costs / max(abs(gross_pnl), 1e-12) if gross_pnl else 0.0,
        "gross_pnl": gross_pnl,
        "costs": costs,
        "net_pnl": net_pnl,
    }

    transitions = {"HOLD→BUY": 0, "BUY→BUY": 0, "BUY→HOLD": 0, "BUY→SELL": 0}
    previous_actions: dict[tuple[str, str], str] = {}
    for checkpoint in checkpoints:
        current_actions: dict[tuple[str, str], str] = {}
        for strategy_id, strategy_payload in (checkpoint.get("strategies") or {}).items():
            for row in strategy_payload.get("decisions") or []:
                key = (str(strategy_id), str(row.get("symbol") or ""))
                action = str(row.get("action") or "HOLD")
                current_actions[key] = action
                if key in previous_actions:
                    label = f"{previous_actions[key]}→{action}"
                    if label in transitions:
                        transitions[label] += 1
        previous_actions = current_actions
    payload["session_metrics"]["transitions"] = transitions

    checkpoint_maps: list[tuple[dict[str, Any], dict[tuple[str, str], dict[str, Any]]]] = []
    for checkpoint in checkpoints:
        mapped: dict[tuple[str, str], dict[str, Any]] = {}
        for strategy_id, strategy_payload in (checkpoint.get("strategies") or {}).items():
            for row in strategy_payload.get("decisions") or []:
                mapped[(str(strategy_id), str(row.get("symbol") or ""))] = row
        checkpoint_maps.append((checkpoint, mapped))

    half_life_hours: list[float] = []
    active_since: dict[tuple[str, str], tuple[str, Any]] = {}
    for checkpoint, mapped in checkpoint_maps:
        as_of_raw = checkpoint.get("as_of")
        try:
            as_of_dt = datetime.fromisoformat(str(as_of_raw).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            as_of_dt = None
        all_keys = set(active_since) | set(mapped)
        for key in all_keys:
            action = str((mapped.get(key) or {}).get("action") or "HOLD")
            prior = active_since.get(key)
            if prior is None:
                if action in {"BUY", "SELL"} and as_of_dt is not None:
                    active_since[key] = (action, as_of_dt)
                continue
            prior_action, started = prior
            if action != prior_action:
                if as_of_dt is not None:
                    half_life_hours.append(
                        max(0.0, (as_of_dt - started).total_seconds() / 3600.0)
                    )
                active_since.pop(key, None)
                if action in {"BUY", "SELL"} and as_of_dt is not None:
                    active_since[key] = (action, as_of_dt)

    posterior: dict[str, list[float]] = {"confirmed": [], "contradicted": []}
    for idx in range(len(checkpoint_maps) - 1):
        _checkpoint, current_map = checkpoint_maps[idx]
        _next_checkpoint, next_map = checkpoint_maps[idx + 1]
        for key, row in current_map.items():
            action = str(row.get("action") or "HOLD").upper()
            if action not in {"BUY", "SELL"} or key not in next_map:
                continue
            trace = row.get("decision_trace") or {}
            timing = trace.get("intraday_timing") or {}
            status = str(timing.get("status") or "")
            if status not in posterior:
                continue
            next_trace = (next_map[key].get("decision_trace") or {})
            current_price = float((trace.get("intraday") or {}).get("price", 0.0) or 0.0)
            next_price = float((next_trace.get("intraday") or {}).get("price", 0.0) or 0.0)
            if current_price <= 0 or next_price <= 0:
                continue
            raw_return = next_price / current_price - 1.0
            posterior[status].append(raw_return if action == "BUY" else -raw_return)

    payload["session_metrics"]["signal_half_life_hours"] = (
        sum(half_life_hours) / len(half_life_hours) if half_life_hours else None
    )
    payload["session_metrics"]["posterior_return_by_timing"] = {
        status: {
            "count": len(values),
            "mean_signed_return": sum(values) / len(values) if values else None,
        }
        for status, values in posterior.items()
    }

    payload["counts_as_promotion_evidence"] = False
    payload["live_execution_enabled"] = False
    payload["orders_emitted"] = False
    payload["automatic_promotion"] = False
    payload["published_at"] = datetime.now(timezone.utc).isoformat()
    _write_object(output_path, payload)
    return payload


def publish_observation_payload(
    data_dir: Path,
    output_path: Path,
    league_config_path: Path | None = None,
) -> dict[str, Any]:
    source = data_dir / "metrics" / "strategy_league" / "experiment_observation.json"
    payload = _load_object(source)
    league_cfg = _load_object(league_config_path)
    expected_league_id = str(league_cfg.get("league_id") or DEFAULT_LEAGUE_ID)
    weekly_model = _weekly_model_summary(data_dir, league_cfg)
    weekly_ready = weekly_model.get("status") == "FROZEN"
    weekly_path_configured = bool(str(league_cfg.get("weekly_model_path") or "").strip())
    weekly_missing = not weekly_ready and (weekly_path_configured or not league_cfg)
    waiting_status = "WAITING_FOR_WEEKLY_MODEL" if weekly_missing else "WAITING_FOR_GENESIS"
    stale_epoch = bool(
        payload
        and league_cfg
        and str(payload.get("league_id") or "") != expected_league_id
    )
    if not payload or stale_epoch:
        payload = {
            "schema_version": 1,
            "league_id": expected_league_id,
            "status": waiting_status,
            "start_session": None,
            "last_session": None,
            "sessions": 0,
            "strategy_league": {
                "status": waiting_status,
                "rows": [],
                "automatic_promotion": False,
                "live_execution_enabled": False,
            },
            "models": {
                "scope": "predictions created on or after Strategy League genesis",
                "horizons": [],
                "weekly_opportunity_challenger": weekly_model,
            },
            "evidence": {
                "prediction_count_since_genesis": 0,
                "reconciled_count_since_genesis": 0,
            },
            "safety": {
                "operational_paper_gateway_used": False,
                "live_execution_enabled": False,
                "automatic_promotion": False,
            },
        }
    else:
        models = payload.setdefault("models", {})
        if isinstance(models, dict):
            models.setdefault("weekly_opportunity_challenger", weekly_model)
    payload.setdefault("safety", {})
    payload["safety"]["operational_paper_gateway_used"] = False
    payload["safety"]["live_execution_enabled"] = False
    payload["safety"]["automatic_promotion"] = False
    _write_object(output_path, payload)
    return payload

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Publish safe Strategy League data to GitHub Pages"
    )
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--output", default="site/data/strategy_league.json")
    parser.add_argument("--league-config", default="config/strategy_league.json")
    parser.add_argument(
        "--observation-output",
        default="site/data/experiment_observation.json",
    )
    parser.add_argument(
        "--live-output",
        default=None,
        help="Optional live snapshot output; defaults beside --output.",
    )
    parser.add_argument(
        "--decision-output",
        default=None,
        help="Optional intraday decision output; defaults beside --output.",
    )
    args = parser.parse_args()
    data_dir = Path(args.data_dir)
    league_config = Path(args.league_config)
    output_path = Path(args.output)
    live_output_path = (
        Path(args.live_output)
        if args.live_output
        else output_path.parent / "strategy_live.json"
    )
    decision_output_path = (
        Path(args.decision_output)
        if args.decision_output
        else output_path.parent / "strategy_decisions_intraday.json"
    )
    payload = publish_league_payload(
        data_dir,
        output_path,
        league_config,
    )
    observation = publish_observation_payload(
        data_dir,
        Path(args.observation_output),
        league_config,
    )
    live = publish_live_payload(
        data_dir,
        live_output_path,
        league_config,
    )
    decisions = publish_intraday_decision_payload(
        data_dir,
        decision_output_path,
        league_config,
    )

    # Research evidence is published beside the league payload, but remains
    # explicitly labeled retrospective/model-OOS/prospective as appropriate.
    from replay.publish_research_site import publish_research_payloads

    research = publish_research_payloads(
        data_dir=data_dir,
        site_data_dir=output_path.parent,
        league_config_path=league_config,
    )
    print(
        json.dumps(
            {
                "status": payload.get("status"),
                "sessions": payload.get("sessions"),
                "leader": (payload.get("summary") or {}).get("strategy_leader"),
                "observation_status": observation.get("status"),
                "live_status": live.get("status"),
                "decision_status": decisions.get("status"),
                "decision_event": decisions.get("event"),
                **research,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
