from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from utils.market_session import checkpoint_times, detect_event, get_session_info

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
    """Build marker keys by workflow type.

    Convention:
    - intraday/event_specific: `<kind>_<YYYY-MM-DD>_<EVENT>`
    - eod: `eod_<YYYY-MM-DD>_CLOSE` (fixed event suffix for read/write parity)
    - other workflows without event: `<kind>_<YYYY-MM-DD>`
    """
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


def _marker_exists(data_dir: Path, kind: str, day: str, event: str) -> bool:
    return _marker_path(data_dir, _marker_key(kind=kind, day=day, event=event)).exists()


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

    # Only run checkpoints that are already due, are still fresh enough to be
    # meaningful, and have not been completed. If several are eligible because
    # a runner was delayed, prefer the most recent due checkpoint rather than
    # fabricating an older market state.
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
    if _marker_path(data_dir, key).exists() or _marker_path(data_dir, legacy_key).exists():
        out.update({"reason": "already_ran", "event": "CLOSE"})
        return out

    close_at = info.market_close
    out["checkpoint_at"] = close_at.isoformat()
    if now < close_at:
        out["reason"] = "not_close_due"
        return out

    lateness = max(0, int((now - close_at).total_seconds() // 60))
    out["lateness_minutes"] = str(lateness)
    if now - close_at > timedelta(minutes=max(0, tolerance_minutes)):
        out.update({"reason": "close_window_missed", "event": "CLOSE"})
        return out

    out.update({"run": "true", "reason": "close_due", "event": "CLOSE"})
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


def mark_run(
    kind: str,
    data_dir: Path,
    event: str | None,
    now: datetime | None = None,
) -> Path:
    current = _as_et(now)
    day = current.date().isoformat()
    marker_event = "CLOSE" if kind == "eod" else event
    key = _marker_key(kind=kind, day=day, event=marker_event)
    marker = _marker_path(data_dir, key)
    marker.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "kind": kind,
        "day": day,
        "event": marker_event,
        "ts": current.isoformat(),
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

    p_mark = sub.add_parser("mark")
    p_mark.add_argument("--kind", required=True)
    p_mark.add_argument("--event", default=None)
    p_mark.add_argument("--data-dir", default="data")

    args = parser.parse_args()

    if args.cmd == "check":
        result = should_run(args.kind, args.tolerance_minutes, args.event, Path(args.data_dir))
        _write_outputs(result)
    elif args.cmd == "mark":
        path = mark_run(args.kind, Path(args.data_dir), args.event)
        _write_outputs({"marker": str(path)})


if __name__ == "__main__":
    main()
