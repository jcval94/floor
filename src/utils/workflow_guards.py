from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from utils.market_session import (
    checkpoint_times,
    detect_event,
    get_session_info,
    required_market_session_at,
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

    eligible = [
        (event, timestamp)
        for event, timestamp in due
        if now - timestamp <= tolerance and not _marker_exists(data_dir, "intraday", day, event)
    ]
    if eligible:
        event, timestamp = max(eligible, key=lambda item: item[1])
        lateness = max(0, int((now - timestamp).total_seconds() // 60))
        out.update(
            {
                "run": "true",
                "reason": "checkpoint_due",
                "event": event,
                "checkpoint_at": timestamp.isoformat(),
                "lateness_minutes": str(lateness),
                "required_market_session": _required_session("intraday", timestamp),
            }
        )
        return out

    unmarked_due = [
        (event, timestamp)
        for event, timestamp in due
        if not _marker_exists(data_dir, "intraday", day, event)
    ]
    if unmarked_due:
        event, timestamp = max(unmarked_due, key=lambda item: item[1])
        lateness = max(0, int((now - timestamp).total_seconds() // 60))
        out.update(
            {
                "reason": "checkpoint_missed",
                "event": event,
                "checkpoint_at": timestamp.isoformat(),
                "lateness_minutes": str(lateness),
                "required_market_session": _required_session("intraday", timestamp),
            }
        )
        return out

    event, timestamp = max(due, key=lambda item: item[1])
    out.update(
        {
            "reason": "already_ran",
            "event": event,
            "checkpoint_at": timestamp.isoformat(),
            "lateness_minutes": str(max(0, int((now - timestamp).total_seconds() // 60))),
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
    return {
        "run": "true",
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
    expected = checkpoint_times(info).get(event)
    if expected is None:
        raise RuntimeError(f"Accepted event is invalid for session {session_day}: {event}")
    if abs((expected - checkpoint).total_seconds()) > 1:
        raise RuntimeError(
            "Accepted checkpoint timestamp changed: "
            f"event={event} expected={expected.isoformat()} accepted={checkpoint.isoformat()}"
        )
    if kind == "eod" and event != "CLOSE":
        raise RuntimeError("EOD accepted context must use CLOSE")

    exists = _marker_exists(data_dir, kind, session_day, event)
    return {
        "run": "false" if exists else "true",
        "reason": "already_ran" if exists else "accepted_context",
        "event": event,
        "session_day": session_day,
        "checkpoint_at": checkpoint.isoformat(),
        "lateness_minutes": "",
        "required_market_session": _required_session(kind, checkpoint),
    }


def mark_run(
    kind: str,
    data_dir: Path,
    event: str | None,
    now: datetime | None = None,
    *,
    session_day: str | None = None,
    checkpoint_at: str | None = None,
) -> Path:
    current = _as_et(now)
    day = session_day or current.date().isoformat()
    marker_event = "CLOSE" if kind == "eod" else event
    key = _marker_key(kind=kind, day=day, event=marker_event)
    marker = _marker_path(data_dir, key)
    marker.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "kind": kind,
        "day": day,
        "event": marker_event,
        "checkpoint_at": checkpoint_at,
        "completed_at": current.isoformat(),
        "run_id": os.getenv("GITHUB_RUN_ID", "local"),
        "workflow": os.getenv("GITHUB_WORKFLOW", "local"),
    }
    marker.write_text(json.dumps(payload), encoding="utf-8")
    return marker


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_check = sub.add_parser("check")
    p_check.add_argument("--kind", required=True)
    p_check.add_argument("--event", default=None)
    p_check.add_argument("--tolerance-minutes", type=int, default=20)
    p_check.add_argument("--data-dir", default="data")

    p_context = sub.add_parser("resolve-context")
    p_context.add_argument("--event", required=True)
    p_context.add_argument("--reason", default="resolved_context")

    p_validate = sub.add_parser("validate-context")
    p_validate.add_argument("--kind", required=True, choices=["intraday", "eod"])
    p_validate.add_argument("--event", required=True)
    p_validate.add_argument("--session-day", required=True)
    p_validate.add_argument("--checkpoint-at", required=True)
    p_validate.add_argument("--data-dir", default="data")

    p_mark = sub.add_parser("mark")
    p_mark.add_argument("--kind", required=True)
    p_mark.add_argument("--event", default=None)
    p_mark.add_argument("--session-day", default=None)
    p_mark.add_argument("--checkpoint-at", default=None)
    p_mark.add_argument("--data-dir", default="data")

    args = parser.parse_args()

    if args.cmd == "check":
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
            )
        )
    elif args.cmd == "mark":
        path = mark_run(
            args.kind,
            Path(args.data_dir),
            args.event,
            session_day=args.session_day,
            checkpoint_at=args.checkpoint_at,
        )
        _write_outputs({"marker": str(path)})


if __name__ == "__main__":
    main()
