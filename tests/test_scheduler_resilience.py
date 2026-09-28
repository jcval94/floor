from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def _text(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def test_market_crons_avoid_top_of_hour_without_main_push_fanout() -> None:
    intraday = _text("intraday_engine.yml")
    strategy_live = _text("strategy_live.yml")
    eod = _text("eod.yml")
    monitoring = _text("monitoring.yml")

    assert 'cron: "7,37 13-22 * * 1-5"' in intraday
    assert 'cron: "22,52 13-22 * * 1-5"' in intraday
    assert 'cron: "5,35,50 13-22 * * 1-5"' in strategy_live
    assert 'cron: "20 13-22 * * 1-5"' in strategy_live
    assert 'cron: "7,37 20-23 * * 1-5"' in eod
    assert 'cron: "22,52 20-23 * * 1-5"' in eod
    assert 'cron: "11 14-18 * * 1-5"' in monitoring
    assert 'cron: "11 19-23 * * 1-5"' in monitoring
    assert "push:\n    branches: [main]" not in intraday
    assert "push:\n    branches: [main]" not in eod


def test_scheduler_watchdog_only_dispatches_missing_guarded_workflows() -> None:
    workflow = _text("scheduler_watchdog.yml")

    assert 'cron: "35 13-18 * * 1-5"' in workflow
    assert 'cron: "35 19-23 * * 1-5"' in workflow
    assert 'cron: "5 13-23 * * 1-5"' not in workflow
    assert "actions: write" in workflow
    assert "has_recent_or_active_run" in workflow
    assert "dispatch_if_stale intraday_engine.yml 1200 intraday" in workflow
    assert "dispatch_if_stale eod.yml 1200 eod" in workflow
    assert "dispatch_if_stale monitoring.yml 3000 monitoring" in workflow
    assert "gh workflow run" in workflow
    assert "unconditional main-push execution: disabled" in workflow


def test_strategy_live_cross_wakes_watchdog_without_invalidating_primary_evidence() -> None:
    workflow = _text("strategy_live.yml")

    assert "actions: write" in workflow
    assert "Cross-wake scheduler watchdog" in workflow
    assert "github.event.schedule == '20 13-22 * * 1-5'" in workflow
    assert "gh workflow run scheduler_watchdog.yml" in workflow
    assert "for attempt in 1 2 3" in workflow
    assert "::warning::Unable to cross-wake scheduler watchdog" in workflow
