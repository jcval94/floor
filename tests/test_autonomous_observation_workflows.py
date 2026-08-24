from pathlib import Path


def _text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_scheduled_workflows_do_not_poll_every_15_or_30_minutes() -> None:
    eod = _text(".github/workflows/eod.yml")
    intraday = _text(".github/workflows/intraday_engine.yml")
    monitoring = _text(".github/workflows/monitoring.yml")

    assert 'cron: "*/15 ' not in eod
    assert 'cron: "*/30 ' not in monitoring
    assert 'cron: "15,30,45 ' not in intraday
    assert 'cron: "30 13-21 * * 1-5"' in intraday
    assert 'cron: "55 13-21 * * 1-5"' in monitoring
    assert 'cron: "45 20,21 * * 1-5"' in eod


def test_eod_closes_the_prospective_evidence_loop() -> None:
    workflow = _text(".github/workflows/eod.yml")

    assert "--range 5d" in workflow
    assert "reconcile-predictions" in workflow
    assert "league.run_eod" in workflow
    assert "league.experiment_observation" in workflow
    assert "experiment_observation_history.jsonl" in workflow
    assert 'LIVE_TRADING_ENABLED: "true"' not in workflow


def test_bootstrap_is_automatic_but_idempotent() -> None:
    workflow = _text(".github/workflows/strategy_league_bootstrap.yml")

    assert "push:" in workflow
    assert "branches: [main]" in workflow
    assert "Check frozen challenger" in workflow
    assert "exists=true" in workflow
    assert "steps.challenger.outputs.exists != 'true'" in workflow


def test_pages_publish_after_eod_and_include_experiment_evidence() -> None:
    workflow = _text(".github/workflows/pages.yml")

    assert 'workflows: ["eod"]' in workflow
    assert "experiment_observation.json" in workflow
    assert "node --check site/assets/experiment.js" in workflow
    assert "operational_paper_gateway_used" in workflow
