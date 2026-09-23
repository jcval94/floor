from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from monitoring.health_snapshot import build_health_snapshot, write_health_snapshot

ET = ZoneInfo("America/New_York")


def _write_dashboard(
    data_dir: Path,
    as_of: datetime,
    *,
    m3_status: str = "ok",
    model_version: str | None = None,
) -> None:
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
            },
            {
                "symbol": "AAPL",
                "as_of": as_of.isoformat(),
                "event_type": "OPEN",
                "horizon": "m3",
                "m3_status": m3_status,
                "model_version": model_version,
                "m3_block_reason": (
                    "timing confidence below threshold"
                    if m3_status == "timing_abstained"
                    else None
                ),
            },
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


def _write_champion(data_dir: Path, task: str, version: str) -> None:
    path = data_dir / "training" / "models" / f"{task}_champion.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": version}), encoding="utf-8")


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
    # OPEN was 60 minutes ago. Lightweight polling plus the immutable context
    # leaves the engine ample time to catch the accepted checkpoint.
    now_et = datetime(2026, 8, 21, 10, 30, tzinfo=ET)
    _write_dashboard(tmp_path, now_et)
    _write_review(tmp_path)

    payload = build_health_snapshot(tmp_path, now=now_et)

    checkpoint = next(item for item in payload["series"] if item["name"] == "checkpoint_completeness")
    assert checkpoint["status"] == "OK"


def test_missing_checkpoint_degrades_after_scheduler_grace(tmp_path: Path) -> None:
    now_et = datetime(2026, 8, 21, 12, 31, tzinfo=ET)  # OPEN is > 180 minutes old.
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


def test_missed_checkpoint_degrades_after_later_checkpoint_recovers_runtime(
    tmp_path: Path,
) -> None:
    # Mirrors the 2026-09-23 incident: OPEN was irrecoverably missed after a
    # delayed scheduler, but OPEN_PLUS_4H later completed successfully.
    now_et = datetime(2026, 8, 21, 13, 52, tzinfo=ET)
    _write_dashboard(tmp_path, now_et)
    _write_review(tmp_path)
    _write_marker(tmp_path, "2026-08-21", "OPEN_PLUS_4H")

    payload = build_health_snapshot(tmp_path, now=now_et)

    assert payload["status"] == "DEGRADED"
    checkpoint = next(
        item
        for item in payload["series"]
        if item["name"] == "checkpoint_completeness"
    )
    assert checkpoint["status"] == "DEGRADED"
    assert "OPEN->OPEN_PLUS_4H" in checkpoint["detail"]
    assert "superseded" in checkpoint["detail"]


def test_later_unrecovered_checkpoint_still_becomes_critical(tmp_path: Path) -> None:
    now_et = datetime(2026, 8, 21, 17, 30, tzinfo=ET)
    _write_dashboard(tmp_path, now_et)
    _write_review(tmp_path)
    _write_marker(tmp_path, "2026-08-21", "OPEN_PLUS_2H")

    payload = build_health_snapshot(tmp_path, now=now_et)

    assert payload["status"] == "CRITICAL"
    checkpoint = next(
        item
        for item in payload["series"]
        if item["name"] == "checkpoint_completeness"
    )
    assert checkpoint["status"] == "CRITICAL"
    assert "OPEN_PLUS_4H" in checkpoint["detail"]
    assert "OPEN->OPEN_PLUS_2H" in checkpoint["detail"]


def test_complete_elapsed_checkpoints_can_be_healthy(tmp_path: Path) -> None:
    now_et = datetime(2026, 8, 21, 17, 30, tzinfo=ET)
    _write_dashboard(tmp_path, now_et)
    _write_review(tmp_path)
    for event in ("OPEN", "OPEN_PLUS_2H", "OPEN_PLUS_4H", "OPEN_PLUS_6H", "CLOSE"):
        _write_marker(tmp_path, "2026-08-21", event)

    payload = build_health_snapshot(tmp_path, now=now_et)

    assert payload["status"] == "OK"


def test_writer_refreshes_evaluation_time_but_preserves_state_change_time(tmp_path: Path) -> None:
    first_now = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
    second_now = datetime(2026, 8, 22, 12, 30, tzinfo=timezone.utc)
    _write_dashboard(tmp_path, first_now)
    _write_review(tmp_path)
    output = tmp_path / "metrics" / "public_metrics.json"

    first = write_health_snapshot(tmp_path, output, now=first_now)
    second = write_health_snapshot(tmp_path, output, now=second_now)

    assert first["status"] == "OK"
    assert second["status"] == "OK"
    assert second["generated_at"] != first["generated_at"]
    assert second["generated_at"] == second_now.isoformat()
    assert second["state_changed_at"] == first["state_changed_at"]


def test_m3_timing_abstention_degrades_operational_health(tmp_path: Path) -> None:
    now = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
    _write_dashboard(tmp_path, now, m3_status="timing_abstained")
    _write_review(tmp_path)

    payload = build_health_snapshot(tmp_path, now=now)

    assert payload["status"] == "DEGRADED"
    capability = next(
        item for item in payload["series"] if item["name"] == "m3_capability"
    )
    assert capability["status"] == "DEGRADED"
    assert "timing_abstained=1/1" in capability["detail"]

def test_stale_m3_batch_is_reported_as_pending_champion_refresh(tmp_path: Path) -> None:
    now = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
    _write_dashboard(
        tmp_path,
        now,
        m3_status="timing_abstained",
        model_version="value:old-value|timing:old-timing",
    )
    _write_champion(tmp_path, "value", "new-value")
    _write_champion(tmp_path, "timing", "new-timing")
    _write_review(tmp_path)

    payload = build_health_snapshot(tmp_path, now=now)
    capability = next(
        item for item in payload["series"] if item["name"] == "m3_capability"
    )

    assert capability["status"] == "DEGRADED"
    assert "pending champion refresh" in capability["detail"]
    assert "timing capability degraded" not in capability["detail"]


def test_current_m3_batch_still_reports_real_timing_abstention(tmp_path: Path) -> None:
    now = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
    version = "value:v2|timing:t2"
    _write_dashboard(
        tmp_path,
        now,
        m3_status="timing_abstained",
        model_version=version,
    )
    _write_champion(tmp_path, "value", "v2")
    _write_champion(tmp_path, "timing", "t2")
    _write_review(tmp_path)

    payload = build_health_snapshot(tmp_path, now=now)
    capability = next(
        item for item in payload["series"] if item["name"] == "m3_capability"
    )

    assert capability["status"] == "DEGRADED"
    assert "timing_abstained=1/1" in capability["detail"]

