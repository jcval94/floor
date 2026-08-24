from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from floor.calendar import is_early_close as calendar_is_early_close
from floor.calendar import is_market_session

ET = ZoneInfo("America/New_York")

EVENTS = ["OPEN", "OPEN_PLUS_2H", "OPEN_PLUS_4H", "OPEN_PLUS_6H", "CLOSE"]


@dataclass(frozen=True)
class SessionInfo:
    session_day: date
    is_open_day: bool
    is_early_close: bool
    market_open: datetime | None
    market_close: datetime | None


def get_session_info(now: datetime | None = None) -> SessionInfo:
    now = now or datetime.now(tz=ET)
    day = now.date()
    if not is_market_session(day):
        return SessionInfo(day, False, False, None, None)

    market_open = datetime.combine(day, time(9, 30), tzinfo=ET)
    early_close = calendar_is_early_close(day)
    close_t = time(13, 0) if early_close else time(16, 0)
    market_close = datetime.combine(day, close_t, tzinfo=ET)
    return SessionInfo(day, True, early_close, market_open, market_close)


def checkpoint_times(info: SessionInfo) -> dict[str, datetime]:
    if not info.is_open_day:
        return {}
    assert info.market_open and info.market_close
    checkpoints = {
        "OPEN": info.market_open,
        "OPEN_PLUS_2H": info.market_open + timedelta(hours=2),
        "OPEN_PLUS_4H": info.market_open + timedelta(hours=4),
        "OPEN_PLUS_6H": info.market_open + timedelta(hours=6),
        "CLOSE": info.market_close,
    }
    return {
        name: ts
        for name, ts in checkpoints.items()
        if info.market_open <= ts <= info.market_close
    }


def detect_event(now: datetime | None = None, tolerance_minutes: int = 20) -> str | None:
    now = now or datetime.now(tz=ET)
    info = get_session_info(now)
    if not info.is_open_day:
        return None
    checkpoints = checkpoint_times(info)
    for event, ts in checkpoints.items():
        if abs((now - ts).total_seconds()) <= tolerance_minutes * 60:
            return event
    return None


def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--detect-event", action="store_true")
    parser.add_argument("--tolerance-minutes", type=int, default=20)
    args = parser.parse_args()

    now = datetime.now(tz=ET)
    info = get_session_info(now)
    payload = {
        "timestamp_et": now.isoformat(),
        "session_day": info.session_day.isoformat(),
        "is_open_day": info.is_open_day,
        "is_early_close": info.is_early_close,
        "market_open": info.market_open.isoformat() if info.market_open else None,
        "market_close": info.market_close.isoformat() if info.market_close else None,
        "checkpoints": {k: v.isoformat() for k, v in checkpoint_times(info).items()},
    }
    if args.detect_event:
        payload["detected_event"] = detect_event(now, args.tolerance_minutes)

    if args.json:
        print(json.dumps(payload))
    else:
        print(payload)


if __name__ == "__main__":
    _main()
