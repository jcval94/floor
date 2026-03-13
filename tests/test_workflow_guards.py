from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import utils.workflow_guards as workflow_guards
from utils.market_session import get_session_info


@dataclass(frozen=True)
class _FakeSessionInfo:
    session_day: date
    is_open_day: bool = True


class _FixedDateTime:
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 3, 12, 16, 0, tzinfo=tz)


def test_session_info_weekend_closed() -> None:
    dt = datetime(2025, 7, 5, 12, 0, tzinfo=ZoneInfo("America/New_York"))
    info = get_session_info(dt)
    assert info.is_open_day is False


def test_session_info_weekday_open_day() -> None:
    dt = datetime(2025, 7, 7, 12, 0, tzinfo=ZoneInfo("America/New_York"))
    info = get_session_info(dt)
    assert info.is_open_day is True


def test_mark_run_eod_uses_close_suffix_and_event(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(workflow_guards, "datetime", _FixedDateTime)

    marker = workflow_guards.mark_run(kind="eod", data_dir=tmp_path, event=None)

    assert marker.name == "eod_2026-03-12_CLOSE.json"
    payload = marker.read_text(encoding="utf-8")
    assert '"event": "CLOSE"' in payload


def test_should_run_eod_accepts_legacy_marker_key(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(workflow_guards, "get_session_info", lambda now: _FakeSessionInfo(session_day=date(2026, 3, 12)))
    monkeypatch.setattr(workflow_guards, "detect_event", lambda now, tolerance_minutes: "CLOSE")

    legacy = tmp_path / "snapshots" / "workflow_runs" / "eod_2026-03-12.json"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text("{}", encoding="utf-8")

    result = workflow_guards.should_run(kind="eod", tolerance_minutes=25, event=None, data_dir=tmp_path)

    assert result["run"] == "false"
    assert result["reason"] == "already_ran"


def test_should_run_intraday_key_unchanged(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(workflow_guards, "get_session_info", lambda now: _FakeSessionInfo(session_day=date(2026, 3, 12)))
    monkeypatch.setattr(workflow_guards, "detect_event", lambda now, tolerance_minutes: "OPEN")

    marker = tmp_path / "snapshots" / "workflow_runs" / "intraday_2026-03-12_OPEN.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("{}", encoding="utf-8")

    result = workflow_guards.should_run(kind="intraday", tolerance_minutes=20, event=None, data_dir=tmp_path)

    assert result["run"] == "false"
    assert result["reason"] == "already_ran"
