from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_runtime_state_shell_is_syntactically_valid() -> None:
    subprocess.run(
        ["bash", "-n", str(ROOT / "scripts" / "runtime_state.sh")],
        check=True,
    )


def test_generated_runtime_paths_are_ignored_but_model_json_is_allowed() -> None:
    ignore = _text(ROOT / ".gitignore")
    assert "data/*" in ignore
    assert "!data/training/models/" in ignore
    assert "!data/training/models/*.json" in ignore
    assert "*.sqlite" in ignore


def test_operational_workflows_do_not_commit_generated_state_to_git() -> None:
    for filename in (
        "intraday_engine.yml",
        "eod.yml",
        "monitoring.yml",
        "ingest.yml",
        "retrain_assessment.yml",
    ):
        workflow = _text(WORKFLOWS / filename)
        assert "runtime_state.sh restore" in workflow
        if filename != "ingest.yml":
            assert "runtime_state.sh publish" in workflow
        assert "git add data/" not in workflow
        assert "git add \\\n            data/" not in workflow


def test_retrain_execute_commits_only_lightweight_model_registry() -> None:
    workflow = _text(WORKFLOWS / "retrain_execute.yml")
    assert "git add -f data/training/models/*.json" in workflow
    for forbidden in (
        "git add data/training data/market data/persistence",
        "git add data/market",
        "git add data/persistence",
        "git add data/training/modelable_dataset",
        "git add data/training/models_file",
    ):
        assert forbidden not in workflow
    assert "runtime_state.sh restore" in workflow
    assert "runtime_state.sh publish" in workflow


def test_pages_restores_runtime_state_before_publication() -> None:
    workflow = _text(WORKFLOWS / "pages.yml")
    restore_pos = workflow.index("runtime_state.sh restore")
    publish_pos = workflow.index("python -m utils.pages_publish")
    assert restore_pos < publish_pos
    assert "runtime_state_source': 'github_release:runtime-state-v1'" in workflow


def test_history_compaction_is_manual_guarded_and_removes_data_from_all_refs() -> None:
    workflow = _text(WORKFLOWS / "manual_compact_git_history.yml")
    assert "workflow_dispatch:" in workflow
    assert "PURGE_GENERATED_DATA_HISTORY" in workflow
    assert "--path data" in workflow
    assert "--invert-paths" in workflow
    assert "git clone --mirror" in workflow
    assert "refs/heads/*:refs/heads/*" in workflow
    assert "refs/tags/*:refs/tags/*" in workflow
    assert "open_prs" in workflow
    assert "runtime_state.sh publish" in workflow


def test_runtime_state_is_release_backed_and_checksum_verified() -> None:
    script = _text(ROOT / "scripts" / "runtime_state.sh")
    assert 'TAG="${RUNTIME_STATE_TAG:-runtime-state-v1}"' in script
    assert "gh release download" in script
    assert "gh release upload" in script
    assert "sha256sum -c" in script
    assert "data/market" in script
    assert "data/persistence" in script
    assert "data/predictions" in script
    assert "data/training/reviews.jsonl" in script
