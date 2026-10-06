from pathlib import Path


def _text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_scheduled_workflows_use_lightweight_resilient_polling() -> None:
    eod = _text(".github/workflows/eod.yml")
    intraday = _text(".github/workflows/intraday_engine.yml")
    monitoring = _text(".github/workflows/monitoring.yml")
    strategy_live = _text(".github/workflows/strategy_live.yml")

    assert 'cron: "7,22,37,52 13-22 * * 1-5"' in intraday
    assert 'cron: "22,52 13-22 * * 1-5"' not in intraday
    assert "--tolerance-minutes 180" in intraday
    assert "--decision-only" in intraday
    assert "intraday_heartbeat" in intraday
    assert "record_missed_checkpoint" in intraday
    assert "scheduler_missed_after_close" in intraday
    assert "intraday-scheduler-missed-" in intraday
    assert "Upload scheduler-missed evidence" in intraday
    assert "--decision-path" in intraday
    assert "Overlay latest lightweight checkpoint state under writer lock" in intraday
    assert "checkpoint_state.sh restore" in intraday
    assert "validate-context" in intraday
    assert "--checkpoint-at" in intraday
    assert "--required-market-session" in intraday

    assert 'cron: "7,37 20-23 * * 1-5"' in eod
    assert 'cron: "22,52 20-23 * * 1-5"' not in eod
    assert "--tolerance-minutes 360" in eod
    assert "checkpoint_state.sh restore" in eod
    assert "validate-context" in eod
    assert "eod_preclose_base.json" in eod
    assert "Finalize shadow portfolio from authoritative pre-EOD base" in eod

    assert 'cron: "5,35 13-22 * * 1-5"' in strategy_live
    assert "previous_market_session" in strategy_live
    assert "official_state_hash" in strategy_live
    assert "runtime_state.sh restore" in strategy_live

    assert 'cron: "11 14-18 * * 1-5"' in monitoring
    assert 'cron: "11 19-23 * * 1-5"' in monitoring
    assert "intraday-audit-" in monitoring
    assert "eod-audit-" in monitoring
    assert "completed_without_runtime_evidence" in monitoring


def test_monitoring_does_not_hydrate_sqlite_or_run_on_market_holidays() -> None:
    monitoring = _text(".github/workflows/monitoring.yml")

    assert "--kind always_open_day" in monitoring
    assert "make init-dbs" not in monitoring
    assert "sqlite_hydration=disabled" in monitoring
    assert "timeout-minutes: 10" in monitoring


def test_eod_closes_the_prospective_evidence_loop() -> None:
    workflow = _text(".github/workflows/eod.yml")

    assert "--range 5d" in workflow
    assert "reconcile-predictions" in workflow
    assert "league.run_eod" in workflow
    assert "league.experiment_observation" in workflow
    assert "experiment_observation_history.jsonl" in workflow
    assert 'LIVE_TRADING_ENABLED: "true"' not in workflow


def test_bootstrap_requires_explicit_confirmed_genesis_request() -> None:
    workflow = _text(".github/workflows/strategy_league_bootstrap.yml")

    assert "workflow_dispatch:" in workflow
    assert "confirm_league_id:" in workflow
    assert "push:" in workflow
    assert "config/strategy_league_genesis_request.json" in workflow
    assert "Resolve and confirm explicit league genesis" in workflow
    assert "BOOTSTRAP_CLEAN_GENESIS" in workflow
    assert "state but its frozen Weekly artifact is missing" in workflow
    assert 'pip install -e ".[modeling]"' in workflow


def test_pages_publish_only_after_authorized_upstream_evidence() -> None:
    pages = _text(".github/workflows/pages.yml")
    eod = _text(".github/workflows/eod.yml")

    # EOD owns its publication handoff explicitly after durable state is
    # published. Other evidence producers still use evidence-gated
    # workflow_run triggers.
    workflow_run_header = pages.split("push:", 1)[0]
    assert '"eod"' not in workflow_run_header
    assert "runtime_state.sh publish" in eod
    assert "checkpoint_state.sh publish" in eod
    assert 'gh workflow run pages.yml --repo "${GITHUB_REPOSITORY}" --ref main' in eod

    for upstream in (
        "retrain_execute",
        "retrain_assessment",
        "strategy_league_bootstrap",
        "monitoring",
        "capital_challenger_tournament",
        "walk_forward_oos",
        "strategy_live",
        "intraday_engine",
    ):
        assert upstream in workflow_run_header

    for evidence in (
        "retrain-execute-",
        "retrain-assessment-",
        "strategy-league-weekly-model-",
        "monitoring-health-",
        "capital-tournament-",
        "walk-forward-oos-",
        "strategy-live-",
        "intraday-audit-",
    ):
        assert evidence in pages

    assert "checkpoint-state-v1" in pages
    assert "CHECKPOINT_STATE_PAYLOAD_PIN" in pages
    assert "scripts/checkpoint_state.sh restore" in pages
    assert "completed_without_publishable_evidence" in pages
    assert "needs.gate.outputs.publish == 'true'" in pages
    assert "actions: read" in pages
    assert "experiment_observation.json" in pages
    assert "node --check site/assets/experiment.js" in pages
    assert "operational_paper_gateway_used" in pages


def test_retrain_assessment_invalidates_on_model_code_changes() -> None:
    workflow = _text(".github/workflows/retrain_assessment.yml")

    assert "- 'src/models/**'" in workflow
    assert "retrain_assessment_request.json" in workflow
