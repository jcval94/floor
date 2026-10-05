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

    assert 'cron: "7,22,37,52 13-22 * * 1-5"' in intraday
    assert 'cron: "22,52 13-22 * * 1-5"' not in intraday
    assert 'cron: "5,35 13-22 * * 1-5"' in strategy_live
    assert 'cron: "20 13-22 * * 1-5"' not in strategy_live
    assert 'cron: "7,37 20-23 * * 1-5"' in eod
    assert 'cron: "22,52 20-23 * * 1-5"' not in eod
    assert 'cron: "11 14-18 * * 1-5"' in monitoring
    assert 'cron: "11 19-23 * * 1-5"' in monitoring
    assert "push:\n    branches: [main]" not in intraday
    assert "push:\n    branches: [main]" not in eod


def test_scheduler_watchdog_only_dispatches_missing_guarded_workflows() -> None:
    workflow = _text("scheduler_watchdog.yml")

    assert 'cron: "2,17,32,47 13-23 * * 1-5"' in workflow
    assert 'cron: "35 13-18 * * 1-5"' not in workflow
    assert 'cron: "35 19-23 * * 1-5"' not in workflow
    assert "actions: write" in workflow
    assert "has_recent_or_active_run" in workflow
    assert "dispatch_if_stale intraday_engine.yml 1200 intraday" in workflow
    assert 'if [ "$INTRADAY_WINDOW" = "true" ]' in workflow
    assert 'if [ "$MARKET_LIVE" = "true" ]' in workflow
    assert 'if [ "$EOD_WINDOW" = "true" ]' in workflow
    assert "dispatch_if_stale intraday_engine.yml 2100 intraday" not in workflow
    assert "dispatch_if_stale eod.yml 2100 eod" in workflow
    assert "dispatch_if_stale monitoring.yml 3900 monitoring" in workflow
    assert "dispatch_if_stale strategy_live.yml 2100 strategy_live" in workflow
    assert "gh workflow run" in workflow
    assert "unconditional main-push execution: disabled" in workflow


def test_watchdog_retries_transient_api_failures_with_recovery_mesh() -> None:
    workflow = _text("scheduler_watchdog.yml")

    assert "workflow_run:" in workflow
    assert 'workflows: ["strategy_live", "intraday_engine", "monitoring"]' in workflow
    assert 'workflows: ["strategy_live", "intraday_engine", "monitoring", "eod"]' not in workflow
    assert "Do not wake from EOD itself" in workflow
    assert "types: [completed]" in workflow
    assert "branches: [main]" in workflow
    assert "list_runs_with_retry" in workflow
    assert "dispatch_with_retry" in workflow
    assert "for attempt in 1 2 3" in workflow
    assert "inspection_failed" in workflow
    assert "dispatch_failed" in workflow
    assert "if: always()" in workflow


def test_strategy_live_uses_half_hour_cadence_without_embedded_cross_wake() -> None:
    workflow = _text("strategy_live.yml")

    assert 'cron: "5,35 13-22 * * 1-5"' in workflow
    assert "watchdog_cross_wake:" not in workflow
    assert "gh workflow run scheduler_watchdog.yml" not in workflow


def test_intraday_watchdog_budget_is_shorter_than_logical_heartbeat_cadence() -> None:
    workflow = _text("scheduler_watchdog.yml")

    # Logical heartbeats are 30 minutes apart while independent wake attempts
    # occur every 15 minutes. The freshness budget must therefore stay below
    # one logical heartbeat interval.
    assert "dispatch_if_stale intraday_engine.yml 1200 intraday" in workflow
    assert "dispatch_if_stale strategy_live.yml 2100 strategy_live" in workflow
    assert 'cron: "2,17,32,47 13-23 * * 1-5"' in workflow


def test_watchdog_counts_only_recent_active_or_successful_runs() -> None:
    workflow = _text("scheduler_watchdog.yml")

    assert "--json createdAt,status,conclusion,event,databaseId,headSha" in workflow
    assert 'active_cutoff=$((cutoff - 1800))' in workflow
    assert '--argjson active_cutoff "$active_cutoff"' in workflow
    assert '.conclusion == "success"' in workflow
    assert '.status != "completed"' in workflow
    assert '>= $active_cutoff' in workflow
    assert '>= $cutoff' in workflow


def test_split_crons_never_contain_literal_newline_escape() -> None:
    for name in (
        "intraday_engine.yml",
        "eod.yml",
        "monitoring.yml",
        "scheduler_watchdog.yml",
        "strategy_live.yml",
    ):
        assert "\\n    - cron:" not in _text(name)


def test_watchdog_circuit_breaks_known_deterministic_eod_failures_without_self_wake() -> None:
    workflow = _text("scheduler_watchdog.yml")

    assert "deterministic_failure_circuit_open" in workflow
    assert '.headSha == $sha' in workflow
    assert 'gh run view "$run_id" --repo "$repo" --log-failed' in workflow
    assert (
        "Strategy League frozen contract changed; create a new league_id "
        "instead of rewriting history"
    ) in workflow
    assert "Material shadow/EOD divergence:" in workflow
    assert "Shadow portfolio base is not T-1 authoritative state:" in workflow
    assert "Frozen Weekly challenger is missing." in workflow
    assert "Frozen Weekly challenger is an unresolved LFS pointer:" in workflow
    assert "Frozen Weekly challenger hash changed; create a new league_id instead of rewriting history" in workflow
    assert "Frozen Weekly challenger must keep canonical_serving_enabled=false" in workflow
    assert "Strategy League weekly model contract invalid:" in workflow
    assert "blocked_deterministic_same_sha" in workflow
    assert "failing open to normal recovery" in workflow


def test_watchdog_uses_new_york_market_calendar_not_raw_utc_hour() -> None:
    workflow = _text("scheduler_watchdog.yml")

    assert "Resolve NYSE recovery windows" in workflow
    assert "from utils.market_session import ET, get_session_info" in workflow
    assert "market_live = info.market_open <= now < info.market_close" in workflow
    assert "info.market_close + timedelta(minutes=20)" in workflow
    assert 'utc_hour="$(date -u +%H)"' not in workflow


def test_intraday_engine_has_redundant_wakes_but_one_nominal_heartbeat_grid() -> None:
    workflow = _text("intraday_engine.yml")

    assert 'cron: "7,22,37,52 13-22 * * 1-5"' in workflow
    assert "--decision-only" in workflow
    assert "needs.gate.outputs.kind == 'intraday'" in workflow
    assert '--marker-kind "${{ needs.gate.outputs.kind }}"' in workflow
