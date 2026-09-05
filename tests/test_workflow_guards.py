from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import utils.workflow_guards as workflow_guards
from utils.market_session import get_session_info

ET = ZoneInfo("America/New_York")


def _write_marker(data_dir: Path, day: str, event: str, *, kind: str = "intraday") -> Path:
    suffix = "CLOSE" if kind == "eod" else event
    path = data_dir / "snapshots" / "workflow_runs" / f"{kind}_{day}_{suffix}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    return path


def test_session_info_weekend_closed() -> None:
    dt = datetime(2025, 7, 5, 12, 0, tzinfo=ET)
    info = get_session_info(dt)
    assert info.is_open_day is False


def test_session_info_weekday_open_day() -> None:
    dt = datetime(2025, 7, 7, 12, 0, tzinfo=ET)
    info = get_session_info(dt)
    assert info.is_open_day is True


def test_mark_run_eod_uses_close_suffix_and_event(tmp_path: Path) -> None:
    now = datetime(2026, 3, 12, 16, 5, tzinfo=ET)

    marker = workflow_guards.mark_run(kind="eod", data_dir=tmp_path, event=None, now=now)

    assert marker.name == "eod_2026-03-12_CLOSE.json"
    payload = marker.read_text(encoding="utf-8")
    assert '"event": "CLOSE"' in payload


def test_should_run_eod_accepts_legacy_marker_key(tmp_path: Path) -> None:
    legacy = tmp_path / "snapshots" / "workflow_runs" / "eod_2026-03-12.json"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text("{}", encoding="utf-8")

    result = workflow_guards.should_run(
        kind="eod",
        tolerance_minutes=360,
        event=None,
        data_dir=tmp_path,
        now=datetime(2026, 3, 12, 16, 30, tzinfo=ET),
    )

    assert result["run"] == "false"
    assert result["reason"] == "already_ran"


def test_intraday_duplicate_is_suppressed(tmp_path: Path) -> None:
    _write_marker(tmp_path, "2026-03-12", "OPEN")

    result = workflow_guards.should_run(
        kind="intraday",
        tolerance_minutes=90,
        event=None,
        data_dir=tmp_path,
        now=datetime(2026, 3, 12, 10, 15, tzinfo=ET),
    )

    assert result["run"] == "false"
    assert result["reason"] == "already_ran"
    assert result["event"] == "OPEN"


def test_intraday_delayed_runner_still_runs_due_checkpoint(tmp_path: Path) -> None:
    result = workflow_guards.should_run(
        kind="intraday",
        tolerance_minutes=90,
        event=None,
        data_dir=tmp_path,
        now=datetime(2026, 3, 12, 10, 25, tzinfo=ET),
    )

    assert result["run"] == "true"
    assert result["reason"] == "checkpoint_due"
    assert result["event"] == "OPEN"
    assert result["lateness_minutes"] == "55"


def test_intraday_never_runs_future_checkpoint(tmp_path: Path) -> None:
    _write_marker(tmp_path, "2026-03-12", "OPEN")

    result = workflow_guards.should_run(
        kind="intraday",
        tolerance_minutes=90,
        event=None,
        data_dir=tmp_path,
        now=datetime(2026, 3, 12, 10, 45, tzinfo=ET),
    )

    assert result["run"] == "false"
    assert result["event"] == "OPEN"
    assert result["reason"] == "already_ran"


def test_intraday_prefers_most_recent_due_checkpoint_after_long_delay(tmp_path: Path) -> None:
    result = workflow_guards.should_run(
        kind="intraday",
        tolerance_minutes=90,
        event=None,
        data_dir=tmp_path,
        now=datetime(2026, 3, 12, 11, 50, tzinfo=ET),
    )

    assert result["run"] == "true"
    assert result["event"] == "OPEN_PLUS_2H"
    assert result["lateness_minutes"] == "20"


def test_intraday_reports_checkpoint_missed_instead_of_green_skip(tmp_path: Path) -> None:
    result = workflow_guards.should_run(
        kind="intraday",
        tolerance_minutes=90,
        event=None,
        data_dir=tmp_path,
        now=datetime(2026, 3, 12, 11, 10, tzinfo=ET),
    )

    assert result["run"] == "false"
    assert result["reason"] == "checkpoint_missed"
    assert result["event"] == "OPEN"
    assert result["lateness_minutes"] == "100"


def test_eod_can_retry_well_after_close(tmp_path: Path) -> None:
    result = workflow_guards.should_run(
        kind="eod",
        tolerance_minutes=360,
        event=None,
        data_dir=tmp_path,
        now=datetime(2026, 3, 12, 18, 30, tzinfo=ET),
    )

    assert result["run"] == "true"
    assert result["reason"] == "close_due"
    assert result["event"] == "CLOSE"
    assert result["lateness_minutes"] == "150"


def test_eod_before_close_is_expected_skip(tmp_path: Path) -> None:
    result = workflow_guards.should_run(
        kind="eod",
        tolerance_minutes=360,
        event=None,
        data_dir=tmp_path,
        now=datetime(2026, 3, 12, 15, 45, tzinfo=ET),
    )

    assert result["run"] == "false"
    assert result["reason"] == "not_close_due"


def test_eod_expired_window_is_explicit_failure_reason(tmp_path: Path) -> None:
    result = workflow_guards.should_run(
        kind="eod",
        tolerance_minutes=360,
        event=None,
        data_dir=tmp_path,
        now=datetime(2026, 3, 12, 23, 0, tzinfo=ET),
    )

    assert result["run"] == "false"
    assert result["reason"] == "close_window_missed"
    assert result["event"] == "CLOSE"


def test_early_close_uses_actual_market_close(tmp_path: Path) -> None:
    # Christmas Eve 2026 closes at 13:00 ET.
    result = workflow_guards.should_run(
        kind="eod",
        tolerance_minutes=360,
        event=None,
        data_dir=tmp_path,
        now=datetime(2026, 12, 24, 13, 40, tzinfo=ET),
    )

    assert result["run"] == "true"
    assert result["event"] == "CLOSE"
    assert result["lateness_minutes"] == "40"
