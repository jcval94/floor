from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_ci_runs_once_per_pr_update_and_pytest_once_per_job() -> None:
    workflow = _text(".github/workflows/ci.yml")

    assert "push:\n    branches: [main]" in workflow
    assert "pull_request:\n    branches: [main]" in workflow
    assert "github.event_name == 'pull_request' && github.event.pull_request.number || github.sha" in workflow
    assert "cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in workflow
    assert workflow.count("pytest -q") == 1
    assert "--cov=src" in workflow
    assert "--cov-fail-under=60" in workflow


def test_intraday_audit_does_not_archive_accumulated_runtime_state() -> None:
    workflow = _text(".github/workflows/intraday_engine.yml")
    upload = workflow.split("Upload immutable per-run audit artifact", 1)[1]

    assert "utils.intraday_audit" in workflow
    assert 'path: ${{ runner.temp }}/intraday-audit' in upload
    assert "if-no-files-found: error" in upload
    for forbidden in (
        "data/predictions",
        "data/signals",
        "data/persistence/app.sqlite",
        "data/market/market_data.sqlite",
    ):
        assert forbidden not in upload
