from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from monitoring.health_snapshot import build_health_snapshot, write_health_snapshot

ET = ZoneInfo("America/New_York")


def _write_dashboard(data_dir: Path, as_of: datetime) -> None:
    path = data_dir / "reports" / "dashboard.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": as_of.astimezone(timezone.utc).isoformat(),
        "latest_predictions": [
            {
                "symbol": "AAPL",
                "as_of": as_of.isoformat(),
                "event_type": "OPEN",
                "horizon": "d1",
            }
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_review(data_dir: Path, status: str = "OK", recommendation: str = "KEEP") -> None:
    path = data_dir / "training" / "review_summary_latest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "suite_status": status,
                "suite_recommendation": recommendation,
            }
        ),
        encoding="utf-8",
    )


def _write_marker(data_dir: Path, day: str, event: str) -> None:
    path = data_dir / "snapshots" / "workflow_runs" / f"intraday_{day}_{event}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")


def test_missing_dashboard_is_critical(tmp_path: Path) -> None:
    now = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)  # Saturday
    _write_review(tmp_path)

    payload = build_health_snapshot(tmp_path, now=now)

    assert payload["status"] == "CRITICAL"
    assert any(item["name"] == "dashboard_present" and item["status"] == "CRITICAL" for item in payload["series"])
    assert payload["series"]


def test_fresh_weekend_snapshot_is_ok(tmp_path: Path) -> None:
    now = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)  # Saturday
    _write_dashboard(tmp_path, now)
    _write_review(tmp_path)

    payload = build_health_snapshot(tmp_path, now=now)

    assert payload["status"] == "OK"
    assert all(item["status"] == "OK" for item in payload["series"])


def test_retrain_alert_degrades_health_instead_of_reporting_ok(tmp_path: Path) -> None:
    now = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
    _write_dashboard(tmp_path, now)
    _write_review(tmp_path, status="ALERT", recommendation="RETRAIN_NOW")

    payload = build_health_snapshot(tmp_path, now=now)

    assert payload["status"] == "DEGRADED"
    assert any(item["name"] == "retraining_review" and item["status"] == "DEGRADED" for item in payload["series"])


def test_scheduler_delay_inside_grace_does_not_raise_false_alarm(tmp_path: Path) -> None:
    # OPEN was 60 minutes ago. GitHub Actions can be delayed; the engine still
    # has time to catch the checkpoint inside its 90-minute execution window.
    now_et = datetime(2026, 8, 21, 10, 30, tzinfo=ET)
    _write_dashboard(tmp_path, now_et)
    _write_review(tmp_path)

    payload = build_health_snapshot(tmp_path, now=now_et)

    checkpoint = next(item for item in payload["series"] if item["name"] == "checkpoint_completeness")
    assert checkpoint["status"] == "OK"


def test_missing_checkpoint_degrades_after_scheduler_grace(tmp_path: Path) -> None:
    now_et = datetime(2026, 8, 21, 11, 0, tzinfo=ET)  # OPEN is 90 minutes old.
    _write_dashboard(tmp_path, now_et)
    _write_review(tmp_path)

    payload = build_health_snapshot(tmp_path, now=now_et)

    checkpoint = next(item for item in payload["series"] if item["name"] == "checkpoint_completeness")
    assert checkpoint["status"] == "DEGRADED"
    assert "OPEN" in checkpoint["detail"]


def test_missing_elapsed_checkpoints_are_critical_after_hard_deadline(tmp_path: Path) -> None:
    now_et = datetime(2026, 8, 21, 17, 30, tzinfo=ET)
    _write_dashboard(tmp_path, now_et)
    _write_review(tmp_path)

    payload = build_health_snapshot(tmp_path, now=now_et)

    assert payload["status"] == "CRITICAL"
    checkpoint = next(item for item in payload["series"] if item["name"] == "checkpoint_completeness")
    assert checkpoint["status"] == "CRITICAL"
    assert "OPEN" in checkpoint["detail"]


def test_complete_elapsed_checkpoints_can_be_healthy(tmp_path: Path) -> None:
    now_et = datetime(2026, 8, 21, 17, 30, tzinfo=ET)
    _write_dashboard(tmp_path, now_et)
    _write_review(tmp_path)
    for event in ("OPEN", "OPEN_PLUS_2H", "OPEN_PLUS_4H", "OPEN_PLUS_6H", "CLOSE"):
        _write_marker(tmp_path, "2026-08-21", event)

    payload = build_health_snapshot(tmp_path, now=now_et)

    assert payload["status"] == "OK"


def test_writer_does_not_churn_timestamp_when_semantics_unchanged(tmp_path: Path) -> None:
    first_now = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
    second_now = datetime(2026, 8, 22, 12, 30, tzinfo=timezone.utc)
    _write_dashboard(tmp_path, first_now)
    _write_review(tmp_path)
    output = tmp_path / "metrics" / "public_metrics.json"

    first = write_health_snapshot(tmp_path, output, now=first_now)
    second = write_health_snapshot(tmp_path, output, now=second_now)

    assert first["status"] == "OK"
    assert second["status"] == "OK"
    assert second["generated_at"] == first["generated_at"]
