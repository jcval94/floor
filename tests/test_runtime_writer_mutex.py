from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

WRITER_WORKFLOWS = (
    ROOT / ".github" / "workflows" / "intraday_engine.yml",
    ROOT / ".github" / "workflows" / "eod.yml",
    ROOT / ".github" / "workflows" / "strategy_league_bootstrap.yml",
)


def test_runtime_writer_mutex_script_has_valid_bash_syntax() -> None:
    subprocess.run(
        ["bash", "-n", str(ROOT / "scripts" / "runtime_writer_lock.sh")],
        check=True,
    )


def test_runtime_writers_use_explicit_mutex_not_lossy_shared_concurrency() -> None:
    for path in WRITER_WORKFLOWS:
        text = path.read_text(encoding="utf-8")
        assert "group: floor-runtime-state-writer" not in text
        assert "bash scripts/runtime_writer_lock.sh acquire" in text
        assert "bash scripts/runtime_writer_lock.sh release" in text
        assert "actions: read" in text or "actions: write" in text


def test_mutex_is_acquired_before_runtime_restore() -> None:
    for path in WRITER_WORKFLOWS:
        text = path.read_text(encoding="utf-8")
        assert text.index("runtime_writer_lock.sh acquire") < text.index(
            "runtime_state.sh restore"
        )


def test_mutex_release_is_failure_safe() -> None:
    for path in WRITER_WORKFLOWS:
        text = path.read_text(encoding="utf-8")
        release_at = text.index("runtime_writer_lock.sh release")
        prefix = text[max(0, release_at - 180) : release_at]
        assert "if: always()" in prefix


def test_mutex_contract_has_owner_and_stale_recovery() -> None:
    script = (ROOT / "scripts" / "runtime_writer_lock.sh").read_text(
        encoding="utf-8"
    )
    assert "GITHUB_RUN_ID" in script
    assert "actions/runs/$owner" in script
    assert 'status" == "completed' in script
    assert "STALE_SECONDS" in script
    assert "safe_delete_if_owner" in script
    assert "releases/$release_id" in script
    assert "gh release delete" not in script


def test_mutex_stale_age_uses_current_release_publication_time() -> None:
    script = (ROOT / "scripts" / "runtime_writer_lock.sh").read_text(
        encoding="utf-8"
    )
    assert ".published_at // .created_at // empty" in script
