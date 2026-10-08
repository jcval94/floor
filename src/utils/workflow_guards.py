from __future__ import annotations

import argparse
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from utils.market_session import (
    checkpoint_times,
    detect_event,
    get_session_info,
    required_market_session_at,
    session_info_for_day,
)

ET = ZoneInfo("America/New_York")


def _write_outputs(values: dict[str, str]) -> None:
    output_file = os.getenv("GITHUB_OUTPUT")
    if not output_file:
        for k, v in values.items():
            print(f"{k}={v}")
        return
    with open(output_file, "a", encoding="utf-8") as f:
        for k, v in values.items():
            f.write(f"{k}={v}\n")


def _marker_path(base_dir: Path, key: str) -> Path:
    return base_dir / "snapshots" / "workflow_runs" / f"{key}.json"


def _marker_key(kind: str, day: str, event: str | None = None) -> str:
    if kind == "eod":
        return f"eod_{day}_CLOSE"
    if event:
        return f"{kind}_{day}_{event}"
    return f"{kind}_{day}"


def _as_et(now: datetime | None) -> datetime:
    current = now or datetime.now(tz=ET)
    if current.tzinfo is None:
        current = current.replace(tzinfo=ET)
    return current.astimezone(ET)


def _parse_dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ET)
    return parsed.astimezone(ET)


def _marker_exists(data_dir: Path, kind: str, day: str, event: str) -> bool:
    return _marker_path(data_dir, _marker_key(kind=kind, day=day, event=event)).exists()


def _missing_marker_path(data_dir: Path, day: str, event: str) -> Path:
    return _marker_path(data_dir, f"intraday_missing_{day}_{event}")


def _checkpoint_finalized(data_dir: Path, day: str, event: str) -> bool:
    return _marker_exists(data_dir, "intraday", day, event) or _missing_marker_path(
        data_dir, day, event
    ).exists()


def _heartbeat_event(timestamp: datetime) -> str:
    return f"HEARTBEAT_{timestamp.astimezone(ET).strftime('%H%M')}"


def _oldest_pending_heartbeat(
    *,
    now: datetime,
    market_open: datetime | None,
    market_close: datetime | None,
    official_checkpoints: dict[str, datetime],
    data_dir: Path,
    day: str,
) -> datetime | None:
    """Return the oldest due 30-minute heartbeat still missing in the live session.

    GitHub schedule delivery is best-effort. If one or more wake opportunities
    are delayed, the next live-session wake drains missing heartbeat slots
    chronologically using point-in-time bars. Heartbeats are never backfilled
    after the market closes; post-close catch-up is reserved for official
    OPEN/+2h/+4h/+6h/CLOSE checkpoints.
    """

    if market_open is None or market_close is None:
        return None
    if now < market_open or now >= market_close:
        return None

    official_times = set(official_checkpoints.values())
    slot = market_open
    while slot <= now and slot < market_close:
        if slot not in official_times:
            event = _heartbeat_event(slot)
            if not _marker_exists(data_dir, "intraday_heartbeat", day, event):
                return slot
        slot += timedelta(minutes=30)
    return None


def _required_session(kind: str, checkpoint_at: datetime) -> str:
    if kind == "eod":
        return checkpoint_at.astimezone(ET).date().isoformat()
    return required_market_session_at(checkpoint_at).isoformat()


def _intraday_decision(
    *,
    now: datetime,
    tolerance_minutes: int,
    data_dir: Path,
    out: dict[str, str],
) -> dict[str, str]:
    info = get_session_info(now)
    checkpoints = checkpoint_times(info)
    if not checkpoints:
        out["reason"] = "no_checkpoints"
        return out

    day = info.session_day.isoformat()
    tolerance = timedelta(minutes=max(0, tolerance_minutes))
    due = [(event, timestamp) for event, timestamp in checkpoints.items() if timestamp <= now]
    if not due:
        out["reason"] = "no_checkpoint_due"
        return out

    # Catch up chronologically. Every accepted checkpoint is reconstructed
    # strictly point-in-time by the downstream engine, so scheduler delay is not
    # a reason to skip older evidence. The half-hour heartbeat keeps draining
    # this queue until all due checkpoints are finalized.
    pending = [
        (event, timestamp)
        for event, timestamp in due
        if not _checkpoint_finalized(data_dir, day, event)
    ]

    # Intraday evidence must be produced while the session is live. The only
    # post-close exception is the nominal CLOSE checkpoint, which gets a short
    # 20-minute completion grace period. If an earlier checkpoint is still
    # missing at/after close, surface the miss instead of reconstructing a day
    # later and making the session look healthy.
    if pending and info.market_close is not None and now >= info.market_close:
        event, timestamp = min(pending, key=lambda item: item[1])
        close_grace = info.market_close + timedelta(minutes=20)
        if event != "CLOSE" or now > close_grace:
            lateness = max(0, int((now - timestamp).total_seconds() // 60))
            out.update(
                {
                    "kind": "intraday",
                    "reason": "checkpoint_missed",
                    "event": event,
                    "checkpoint_at": timestamp.isoformat(),
                    "lateness_minutes": str(lateness),
                    "required_market_session": _required_session(
                        "intraday",
                        timestamp,
                    ),
                }
            )
            return out

    if not pending:
        heartbeat_at = _oldest_pending_heartbeat(
            now=now,
            market_open=info.market_open,
            market_close=info.market_close,
            official_checkpoints=checkpoints,
            data_dir=data_dir,
            day=day,
        )
        if heartbeat_at is not None:
            heartbeat_event = _heartbeat_event(heartbeat_at)
            heartbeat_done = _marker_exists(
                data_dir,
                "intraday_heartbeat",
                day,
                heartbeat_event,
            )
            out.update(
                {
                    "kind": "intraday_heartbeat",
                    "reason": (
                        "heartbeat_already_ran"
                        if heartbeat_done
                        else "heartbeat_due"
                    ),
                    "event": heartbeat_event,
                    "checkpoint_at": heartbeat_at.isoformat(),
                    "lateness_minutes": str(
                        max(0, int((now - heartbeat_at).total_seconds() // 60))
                    ),
                    "required_market_session": _required_session(
                        "intraday_heartbeat",
                        heartbeat_at,
                    ),
                }
            )
            if not heartbeat_done:
                out["run"] = "true"
            return out

        event, timestamp = max(due, key=lambda item: item[1])
        lateness = max(0, int((now - timestamp).total_seconds() // 60))
        out.update(
            {
                "kind": "intraday",
                "reason": "already_ran",
                "event": event,
                "checkpoint_at": timestamp.isoformat(),
                "lateness_minutes": str(lateness),
                "required_market_session": _required_session("intraday", timestamp),
            }
        )
        return out

    event, timestamp = min(pending, key=lambda item: item[1])
    lateness = max(0, int((now - timestamp).total_seconds() // 60))
    reason = "checkpoint_due" if now - timestamp <= tolerance else "checkpoint_catchup"
    out.update(
        {
            "run": "true",
            "kind": "intraday",
            "reason": reason,
            "event": event,
            "checkpoint_at": timestamp.isoformat(),
            "lateness_minutes": str(lateness),
            "required_market_session": _required_session("intraday", timestamp),
        }
    )
    return out


def _eod_decision(
    *,
    now: datetime,
    tolerance_minutes: int,
    data_dir: Path,
    out: dict[str, str],
) -> dict[str, str]:
    info = get_session_info(now)
    if info.market_close is None:
        out["reason"] = "market_closed"
        return out

    day = info.session_day.isoformat()
    key = _marker_key(kind="eod", day=day, event="CLOSE")
    legacy_key = f"eod_{day}"
    out.update(
        {
            "event": "CLOSE",
            "checkpoint_at": info.market_close.isoformat(),
            "required_market_session": day,
        }
    )
    if _marker_path(data_dir, key).exists() or _marker_path(data_dir, legacy_key).exists():
        out["reason"] = "already_ran"
        return out

    close_at = info.market_close
    if now < close_at:
        out["reason"] = "not_close_due"
        return out

    # Daily bars are only considered complete 20 minutes after official close.
    # Keep the accepted checkpoint timestamp at the official close, but do not
    # launch EOD work before today's daily bar is legally available.
    data_ready_at = close_at + timedelta(minutes=20)
    if now < data_ready_at:
        out["reason"] = "close_data_not_ready"
        return out

    lateness = max(0, int((now - close_at).total_seconds() // 60))
    out["lateness_minutes"] = str(lateness)
    if now - close_at > timedelta(minutes=max(0, tolerance_minutes)):
        out["reason"] = "close_window_missed"
        return out

    out.update({"run": "true", "reason": "close_due"})
    return out



def resolve_eod_recovery_context(
    target_session: str,
    data_dir: Path,
    *,
    now: datetime | None = None,
) -> dict[str, str]:
    """Explicit, single-session, pre-next-open recovery; never a cron fallback."""
    current = _as_et(now)
    try:
        target = date.fromisoformat(target_session)
    except ValueError as exc:
        raise RuntimeError("Recovery target must be a valid YYYY-MM-DD date") from exc
    info = session_info_for_day(target)
    if not info.is_open_day or info.market_close is None:
        raise RuntimeError(f"Recovery target is not a market session: {target_session}")
    if current < info.market_close + timedelta(minutes=20):
        raise RuntimeError("Cannot recover an EOD before its daily bars are due")
    today = get_session_info(current)
    if current.date() > target and today.is_open_day and today.market_open is not None:
        if current >= today.market_open:
            raise RuntimeError(
                "Historical EOD recovery after the next market open is unsafe; "
                "requires a separately audited full-session backfill"
            )
    if (current.date() - target).days > 4:
        raise RuntimeError("Recovery target exceeds four-calendar-day safety window")
    if current.date() < target:
        raise RuntimeError("Recovery target cannot be in the future")
    if _marker_exists(data_dir, "eod", target_session, "CLOSE") or _marker_path(
        data_dir, f"eod_{target_session}"
    ).exists():
        reason = "already_ran"
        run = "false"
    else:
        reason = "explicit_missing_eod_recovery"
        run = "true"
    return {
        "run": run,
        "kind": "eod",
        "reason": reason,
        "event": "CLOSE",
        "session_day": target_session,
        "checkpoint_at": info.market_close.isoformat(),
        "lateness_minutes": str(
            int((current - info.market_close).total_seconds() // 60)
        ),
        "required_market_session": target_session,
    }



def should_run(
    kind: str,
    tolerance_minutes: int,
    event: str | None,
    data_dir: Path,
    now: datetime | None = None,
) -> dict[str, str]:
    current = _as_et(now)
    info = get_session_info(current)
    out = {
        "run": "false",
        "kind": kind,
        "reason": "market_closed",
        "event": "",
        "session_day": info.session_day.isoformat(),
        "checkpoint_at": "",
        "lateness_minutes": "",
        "required_market_session": "",
    }
    if not info.is_open_day:
        return out

    if kind == "intraday":
        return _intraday_decision(
            now=current,
            tolerance_minutes=tolerance_minutes,
            data_dir=data_dir,
            out=out,
        )

    if kind == "eod":
        return _eod_decision(
            now=current,
            tolerance_minutes=tolerance_minutes,
            data_dir=data_dir,
            out=out,
        )

    if kind == "always_open_day":
        out.update({"run": "true", "reason": "open_day"})
        return out

    if kind == "event_specific":
        if not event:
            out["reason"] = "missing_event"
            return out
        detected = detect_event(now=current, tolerance_minutes=tolerance_minutes)
        if detected != event:
            out["reason"] = "event_not_matched"
            return out
        key = _marker_key(kind=kind, day=info.session_day.isoformat(), event=event)
        if _marker_path(data_dir, key).exists():
            out["reason"] = "already_ran"
            out["event"] = event
            return out
        out.update({"run": "true", "reason": "event_matched", "event": event})
        return out

    out["reason"] = "unknown_kind"
    return out


def resolve_event_context(
    event: str,
    *,
    now: datetime | None = None,
    reason: str = "resolved_context",
) -> dict[str, str]:
    current = _as_et(now)
    info = get_session_info(current)
    checkpoints = checkpoint_times(info)
    checkpoint_at = checkpoints.get(event)
    if checkpoint_at is None:
        raise RuntimeError(
            f"Cannot resolve event={event!r} for session_day={info.session_day.isoformat()}"
        )
    if checkpoint_at > current:
        raise RuntimeError(
            "Cannot force a future market checkpoint: "
            f"event={event} checkpoint_at={checkpoint_at.isoformat()} now={current.isoformat()}"
        )
    return {
        "run": "true",
        "kind": "intraday",
        "reason": reason,
        "event": event,
        "session_day": info.session_day.isoformat(),
        "checkpoint_at": checkpoint_at.isoformat(),
        "lateness_minutes": str(
            max(0, int((current - checkpoint_at).total_seconds() // 60))
        ),
        "required_market_session": _required_session("intraday", checkpoint_at),
    }


def validate_accepted_context(
    *,
    kind: str,
    event: str,
    session_day: str,
    checkpoint_at: str,
    data_dir: Path,
    allow_existing_repair: bool = False,
) -> dict[str, str]:
    checkpoint = _parse_dt(checkpoint_at)
    if checkpoint.date().isoformat() != session_day:
        raise RuntimeError(
            "Accepted checkpoint context is inconsistent: "
            f"session_day={session_day} checkpoint_at={checkpoint.isoformat()}"
        )
    info = get_session_info(checkpoint)
    if not info.is_open_day:
        raise RuntimeError(f"Accepted checkpoint is not a market session: {session_day}")

    if kind == "intraday_heartbeat":
        if info.market_open is None or info.market_close is None:
            raise RuntimeError("Heartbeat requires a live market session")
        if not event.startswith("HEARTBEAT_"):
            raise RuntimeError(f"Heartbeat accepted context has invalid event: {event}")
        expected_event = _heartbeat_event(checkpoint)
        if event != expected_event:
            raise RuntimeError(
                "Heartbeat event/timestamp mismatch: "
                f"event={event} expected={expected_event} checkpoint={checkpoint.isoformat()}"
            )
        if not (info.market_open <= checkpoint < info.market_close):
            raise RuntimeError(
                f"Heartbeat must be inside market hours: {checkpoint.isoformat()}"
            )
        elapsed_seconds = (checkpoint - info.market_open).total_seconds()
        if abs(elapsed_seconds % (30 * 60)) > 1:
            raise RuntimeError(
                f"Heartbeat must align to a 30-minute market grid: {checkpoint.isoformat()}"
            )
        if checkpoint in set(checkpoint_times(info).values()):
            raise RuntimeError(
                "Heartbeat must not duplicate an official market checkpoint: "
                f"{checkpoint.isoformat()}"
            )
    else:
        expected = checkpoint_times(info).get(event)
        if expected is None:
            raise RuntimeError(
                f"Accepted event is invalid for session {session_day}: {event}"
            )
        if abs((expected - checkpoint).total_seconds()) > 1:
            raise RuntimeError(
                "Accepted checkpoint timestamp changed: "
                f"event={event} expected={expected.isoformat()} accepted={checkpoint.isoformat()}"
            )
        if kind == "eod" and event != "CLOSE":
            raise RuntimeError("EOD accepted context must use CLOSE")

    exists = _marker_exists(data_dir, kind, session_day, event)
    if allow_existing_repair:
        if kind != "intraday":
            raise RuntimeError("Existing-checkpoint repair is supported only for intraday")
        if not exists:
            raise RuntimeError(
                "Existing-checkpoint repair requires a completed checkpoint marker"
            )
        return {
            "run": "true",
            "kind": kind,
            "reason": "repair_existing_checkpoint",
            "event": event,
            "session_day": session_day,
            "checkpoint_at": checkpoint.isoformat(),
            "lateness_minutes": "",
            "required_market_session": _required_session(kind, checkpoint),
        }
    return {
        "run": "false" if exists else "true",
        "kind": kind,
        "reason": "already_ran" if exists else "accepted_context",
        "event": event,
        "session_day": session_day,
        "checkpoint_at": checkpoint.isoformat(),
        "lateness_minutes": "",
        "required_market_session": _required_session(kind, checkpoint),
    }


def mark_missing_checkpoint(
    data_dir: Path,
    *,
    event: str,
    session_day: str,
    checkpoint_at: str,
    reason: str,
    now: datetime | None = None,
) -> Path:
    current = _as_et(now)
    marker = _missing_marker_path(data_dir, session_day, event)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps(
            {
                "kind": "intraday_missing",
                "day": session_day,
                "event": event,
                "checkpoint_at": checkpoint_at,
                "recorded_at": current.isoformat(),
                "reason": reason,
                "run_id": os.getenv("GITHUB_RUN_ID", "local"),
                "workflow": os.getenv("GITHUB_WORKFLOW", "local"),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return marker


def _decision_evidence(path: Path | None) -> dict[str, object] | None:
    if path is None or not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Invalid decision evidence: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Decision evidence must be an object: {path}")

    summary = payload.get("summary") or {}
    actionable = int(summary.get("actionable_decisions", 0) or 0)
    evaluations = int(summary.get("decisions_evaluated", 0) or 0)
    holds = 0
    buys = 0
    sells = 0
    for strategy_payload in (payload.get("strategies") or {}).values():
        if not isinstance(strategy_payload, dict):
            continue
        counts = strategy_payload.get("action_counts") or {}
        holds += int(counts.get("HOLD", 0) or 0)
        buys += int(counts.get("BUY", 0) or 0)
        sells += int(counts.get("SELL", 0) or 0)
    challenger = payload.get("capital_allocation_challenger") or {}
    return {
        "decision_state": (
            "ACTIONABLE"
            if actionable > 0
            else "HOLD"
            if evaluations > 0
            else "WAITING"
        ),
        "as_of": payload.get("as_of"),
        "evaluations": evaluations,
        "actionable": actionable,
        "holds": holds,
        "buys": buys,
        "sells": sells,
        "challenger_targets": int(challenger.get("target_count", 0) or 0),
        "quote_coverage": summary.get("quote_coverage"),
    }


def mark_run(
    kind: str,
    data_dir: Path,
    event: str | None,
    now: datetime | None = None,
    *,
    session_day: str | None = None,
    checkpoint_at: str | None = None,
    reason: str | None = None,
    lateness_minutes: str | int | None = None,
    decision_path: Path | None = None,
) -> Path:
    current = _as_et(now)
    day = session_day or current.date().isoformat()
    marker_event = "CLOSE" if kind == "eod" else event
    key = _marker_key(kind=kind, day=day, event=marker_event)
    marker = _marker_path(data_dir, key)
    marker.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "kind": kind,
        "day": day,
        "event": marker_event,
        "checkpoint_at": checkpoint_at,
        "completed_at": current.isoformat(),
        "guard_reason": reason,
        "lateness_minutes": lateness_minutes,
        "run_id": os.getenv("GITHUB_RUN_ID", "local"),
        "workflow": os.getenv("GITHUB_WORKFLOW", "local"),
    }
    evidence = _decision_evidence(decision_path)
    if evidence is not None:
        payload["decision_evidence"] = evidence
    marker.write_text(json.dumps(payload), encoding="utf-8")
    return marker


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_recovery = sub.add_parser("recovery-eod-context")
    p_recovery.add_argument("--target-session", default="")
    p_recovery.add_argument("--request", default="")
    p_recovery.add_argument("--data-dir", default="data")

    p_check = sub.add_parser("check")
    p_check.add_argument("--kind", required=True)
    p_check.add_argument("--event", default=None)
    p_check.add_argument("--tolerance-minutes", type=int, default=20)
    p_check.add_argument("--data-dir", default="data")

    p_context = sub.add_parser("resolve-context")
    p_context.add_argument("--event", required=True)
    p_context.add_argument("--reason", default="resolved_context")

    p_validate = sub.add_parser("validate-context")
    p_validate.add_argument(
        "--kind",
        required=True,
        choices=["intraday", "intraday_heartbeat", "eod"],
    )
    p_validate.add_argument("--event", required=True)
    p_validate.add_argument("--session-day", required=True)
    p_validate.add_argument("--checkpoint-at", required=True)
    p_validate.add_argument("--data-dir", default="data")
    p_validate.add_argument("--allow-existing-repair", action="store_true")

    p_mark = sub.add_parser("mark")
    p_mark.add_argument("--kind", required=True)
    p_mark.add_argument("--event", default=None)
    p_mark.add_argument("--session-day", default=None)
    p_mark.add_argument("--checkpoint-at", default=None)
    p_mark.add_argument("--reason", default=None)
    p_mark.add_argument("--lateness-minutes", default=None)
    p_mark.add_argument("--decision-path", default=None)
    p_mark.add_argument("--data-dir", default="data")

    p_missing = sub.add_parser("mark-missing")
    p_missing.add_argument("--event", required=True)
    p_missing.add_argument("--session-day", required=True)
    p_missing.add_argument("--checkpoint-at", required=True)
    p_missing.add_argument("--reason", required=True)
    p_missing.add_argument("--data-dir", default="data")

    args = parser.parse_args()

    if args.cmd == "recovery-eod-context":
        if bool(args.target_session) == bool(args.request):
            raise RuntimeError("Specify exactly one recovery target or request file")
        target = args.target_session
        if args.request:
            request = json.loads(Path(args.request).read_text(encoding="utf-8"))
            if (
                not isinstance(request, dict)
                or request.get("mode") != "recover_missing_eod"
                or request.get("authorization") != "CONFIRM_RECOVER_ONLY"
            ):
                raise RuntimeError("Audited EOD recovery request lacks explicit approval")
            target = str(request.get("target_session") or "")
        _write_outputs(resolve_eod_recovery_context(target, Path(args.data_dir)))
    elif args.cmd == "check":
        result = should_run(args.kind, args.tolerance_minutes, args.event, Path(args.data_dir))
        _write_outputs(result)
    elif args.cmd == "resolve-context":
        _write_outputs(resolve_event_context(args.event, reason=args.reason))
    elif args.cmd == "validate-context":
        _write_outputs(
            validate_accepted_context(
                kind=args.kind,
                event=args.event,
                session_day=args.session_day,
                checkpoint_at=args.checkpoint_at,
                data_dir=Path(args.data_dir),
                allow_existing_repair=args.allow_existing_repair,
            )
        )
    elif args.cmd == "mark-missing":
        path = mark_missing_checkpoint(
            Path(args.data_dir),
            event=args.event,
            session_day=args.session_day,
            checkpoint_at=args.checkpoint_at,
            reason=args.reason,
        )
        _write_outputs({"marker": str(path)})
    elif args.cmd == "mark":
        path = mark_run(
            args.kind,
            Path(args.data_dir),
            args.event,
            session_day=args.session_day,
            checkpoint_at=args.checkpoint_at,
            reason=args.reason,
            lateness_minutes=args.lateness_minutes,
            decision_path=Path(args.decision_path) if args.decision_path else None,
        )
        _write_outputs({"marker": str(path)})


if __name__ == "__main__":
    main()
