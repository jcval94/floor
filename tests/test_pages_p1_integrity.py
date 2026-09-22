from __future__ import annotations

from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def test_pages_republishes_after_evidence_backed_state_updates() -> None:
    workflow = (ROOT / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")

    for upstream in (
        "eod",
        "retrain_execute",
        "retrain_assessment",
        "strategy_league_bootstrap",
        "monitoring",
        "capital_challenger_tournament",
        "walk_forward_oos",
    ):
        assert upstream in workflow

    for evidence in (
        "eod-audit-",
        "retrain-execute-",
        "retrain-assessment-",
        "strategy-league-weekly-model-",
        "monitoring-health-",
        "capital-tournament-",
        "walk-forward-oos-",
    ):
        assert evidence in workflow

    assert "data/training/models/**" in workflow
    assert "bash scripts/research_state.sh restore" in workflow


def test_research_workflows_persist_public_evidence() -> None:
    tournament = (
        ROOT / ".github" / "workflows" / "capital_challenger_tournament.yml"
    ).read_text(encoding="utf-8")
    walk_forward = (
        ROOT / ".github" / "workflows" / "walk_forward_oos.yml"
    ).read_text(encoding="utf-8")
    script = (ROOT / "scripts" / "research_state.sh").read_text(encoding="utf-8")

    for workflow in (tournament, walk_forward):
        assert "bash scripts/research_state.sh restore" in workflow
        assert "bash scripts/research_state.sh publish" in workflow
        assert "contents: write" in workflow
        assert "group: floor-research-state-writer" in workflow

    assert "data/reports/strategy.json" in script
    assert "data/reports/strategy_attribution.json" in script
    assert "data/reports/walk_forward_oos.json" in script
    assert "research-state-v1" in script


def test_model_ui_falls_back_to_labeled_champion_validation_metrics() -> None:
    app = (ROOT / "site" / "assets" / "app.js").read_text(encoding="utf-8")
    assert "Monitoring actual" in app
    assert "Validación del champion" in app
    assert "detail?.validation_metrics" in app


def test_research_state_script_has_valid_bash_syntax() -> None:
    subprocess.run(
        ["bash", "-n", str(ROOT / "scripts" / "research_state.sh")],
        check=True,
    )

def test_tournament_uses_versioned_weekly_model_path_from_config() -> None:
    workflow = (
        ROOT / ".github" / "workflows" / "capital_challenger_tournament.yml"
    ).read_text(encoding="utf-8")

    assert 'config["weekly_model_path"]' in workflow
    assert (
        "artifact=data/metrics/strategy_league/models/"
        "weekly_opportunity_challenger.json"
    ) not in workflow

def test_tournament_runner_uses_league_config_as_weekly_model_source() -> None:
    runner = (ROOT / "src" / "replay" / "capital_tournament.py").read_text(
        encoding="utf-8"
    )

    legacy = (
        "data/metrics/strategy_league/models/"
        "weekly_opportunity_challenger.json"
    )
    assert legacy not in runner
    assert 'league_cfg.get("weekly_model_path")' in runner
    assert 'parser.add_argument("--weekly-model", default=None)' in runner

