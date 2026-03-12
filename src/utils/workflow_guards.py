from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from utils.market_session import detect_event, get_session_info

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


def should_run(kind: str, tolerance_minutes: int, event: str | None, data_dir: Path) -> dict[str, str]:
    now = datetime.now(tz=ET)
    info = get_session_info(now)
    out = {
        "run": "false",
        "reason": "market_closed",
        "event": "",
        "session_day": info.session_day.isoformat(),
    }
    if not info.is_open_day:
        return out

    if kind == "intraday":
        detected = detect_event(now=now, tolerance_minutes=tolerance_minutes)
        if not detected:
            out["reason"] = "no_checkpoint_window"
            return out
        key = f"intraday_{info.session_day.isoformat()}_{detected}"
        marker = _marker_path(data_dir, key)
        if marker.exists():
            out["reason"] = "already_ran"
            out["event"] = detected
            return out
        out.update({"run": "true", "reason": "checkpoint_window", "event": detected})
        return out

    if kind == "eod":
        close_event = detect_event(now=now, tolerance_minutes=tolerance_minutes)
        if close_event != "CLOSE":
            out["reason"] = "not_close_window"
            return out
        key = f"eod_{info.session_day.isoformat()}"
        if _marker_path(data_dir, key).exists():
            out["reason"] = "already_ran"
            out["event"] = "CLOSE"
            return out
        out.update({"run": "true", "reason": "close_window", "event": "CLOSE"})
        return out

    if kind == "always_open_day":
        out.update({"run": "true", "reason": "open_day"})
        return out

    if kind == "event_specific":
        if not event:
            out["reason"] = "missing_event"
            return out
        detected = detect_event(now=now, tolerance_minutes=tolerance_minutes)
        if detected != event:
            out["reason"] = "event_not_matched"
            return out
        key = f"{kind}_{info.session_day.isoformat()}_{event}"
        if _marker_path(data_dir, key).exists():
            out["reason"] = "already_ran"
            out["event"] = event
            return out
        out.update({"run": "true", "reason": "event_matched", "event": event})
        return out

    out["reason"] = "unknown_kind"
    return out


def mark_run(kind: str, data_dir: Path, event: str | None) -> Path:
    now = datetime.now(tz=ET)
    day = now.date().isoformat()
    suffix = f"_{event}" if event else ""
    key = f"{kind}_{day}{suffix}"
    marker = _marker_path(data_dir, key)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"kind": kind, "day": day, "event": event, "ts": now.isoformat()}), encoding="utf-8")
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
