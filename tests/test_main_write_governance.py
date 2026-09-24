from __future__ import annotations

import re
from pathlib import Path


WORKFLOW_DIR = Path(".github/workflows")
DIRECT_GIT_PUSH = re.compile(r"\bgit(?:\s+-C\s+\S+)?\s+push\b")
EXPECTED_GIT_WRITERS = {
    "retrain_execute.yml",
    "manual_compact_git_history.yml",
}


def _workflow_text(name: str) -> str:
    return (WORKFLOW_DIR / name).read_text(encoding="utf-8")


def test_direct_git_writers_are_explicitly_allowlisted() -> None:
    writers = {
        path.name
        for path in WORKFLOW_DIR.glob("*.yml")
        if DIRECT_GIT_PUSH.search(path.read_text(encoding="utf-8"))
    }
    writers.update(
        path.name
        for path in WORKFLOW_DIR.glob("*.yaml")
        if DIRECT_GIT_PUSH.search(path.read_text(encoding="utf-8"))
    )

    assert writers == EXPECTED_GIT_WRITERS


def test_retrain_execute_git_writer_remains_governed() -> None:
    workflow = _workflow_text("retrain_execute.yml")

    assert "if: github.ref_name == 'main'" in workflow
    assert "retrain_execute_request.json" in workflow
    assert "approve_recommended" in workflow
    assert "PUBLISH_CHAMPIONS_TO_MAIN" in workflow
    assert "manual_confirmation_required" in workflow
    assert "git add -f data/training/models/*.json" in workflow
    assert "git push" in workflow


def test_manual_training_cannot_publish_production_champions() -> None:
    workflow = _workflow_text("manual_train_all_models.yml")

    assert "workflow_dispatch:" in workflow
    assert "commit_champions:" in workflow
    assert 'default: "false"' in workflow
    assert "Block manual production champion publication" in workflow
    assert "single authority: retrain_execute.yml" in workflow
    assert "contents: write" not in workflow
    assert "git push" not in workflow
    assert "git commit -m" not in workflow


def test_history_compaction_keeps_destructive_safety_gates() -> None:
    workflow = _workflow_text("manual_compact_git_history.yml")

    assert "PURGE_GENERATED_DATA_HISTORY" in workflow
    assert "protected_branches=" in workflow
    assert "open_prs=" in workflow
    assert "--force-with-lease=" in workflow
    assert "push --dry-run --atomic" in workflow
    assert "push --atomic" in workflow
    assert "push --dry-run --atomic --force --prune" not in workflow
    assert "push --atomic --force --prune" not in workflow


def test_scripts_do_not_hide_additional_git_push_writers() -> None:
    offenders = []
    for path in Path("scripts").rglob("*"):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if DIRECT_GIT_PUSH.search(text):
            offenders.append(path.as_posix())

    assert offenders == []
