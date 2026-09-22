from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def _text(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def test_market_crons_avoid_top_of_hour_and_keep_main_push_backstop() -> None:
    intraday = _text("intraday_engine.yml")
    eod = _text("eod.yml")
    monitoring = _text("monitoring.yml")

    assert 'cron: "7,22,37,52 13-22 * * 1-5"' in intraday
    assert 'cron: "7,22,37,52 20-23 * * 1-5"' in eod
    assert 'cron: "11 14-23 * * 1-5"' in monitoring
    assert "push:\n    branches: [main]" in intraday
    assert "push:\n    branches: [main]" in eod


def test_scheduler_watchdog_dispatches_existing_guarded_workflows() -> None:
    workflow = _text("scheduler_watchdog.yml")

    assert 'cron: "5 13-23 * * 1-5"' in workflow
    assert 'cron: "35 13-23 * * 1-5"' in workflow
    assert "actions: write" in workflow
    assert "gh workflow run intraday_engine.yml" in workflow
    assert "gh workflow run eod.yml" in workflow
    assert "gh workflow run monitoring.yml" in workflow
    assert "checkpoint/EOD guards remain authoritative" in workflow
