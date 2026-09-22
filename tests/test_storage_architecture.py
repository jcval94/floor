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
        if filename not in {"ingest.yml", "monitoring.yml"}:
            assert "runtime_state.sh publish" in workflow
        if filename == "monitoring.yml":
            assert "monitoring_state.sh publish" in workflow
            assert "runtime_state.sh publish" not in workflow
        assert "git add data/" not in workflow
        assert "git add \\\n            data/" not in workflow


def test_authoritative_runtime_writers_are_main_only() -> None:
    for filename in (
        "intraday_engine.yml",
        "eod.yml",
        "ingest.yml",
        "retrain_assessment.yml",
        "retrain_execute.yml",
    ):
        workflow = _text(WORKFLOWS / filename)
        assert "github.ref_name == 'main'" in workflow


def test_intraday_and_eod_revalidate_after_writer_lock() -> None:
    intraday = _text(WORKFLOWS / "intraday_engine.yml")
    eod = _text(WORKFLOWS / "eod.yml")
    assert "Revalidate immutable checkpoint context under writer lock" in intraday
    assert "validate-context" in intraday
    assert "steps.lock_guard.outputs.run == 'true'" in intraday
    assert "cancel-in-progress: false" in intraday
    assert "Revalidate immutable close context under writer lock" in eod
    assert "validate-context" in eod
    assert "steps.lock_guard.outputs.run == 'true'" in eod


def test_ingest_uses_before_after_decision_fingerprint_not_git_head() -> None:
    workflow = _text(WORKFLOWS / "ingest.yml")
    assert "Capture decision-state fingerprint before ingest" in workflow
    assert "ingest_decision_before.json" in workflow
    assert "git diff --quiet -- data/predictions" not in workflow


def test_monitoring_isolated_from_authoritative_runtime_writer() -> None:
    workflow = _text(WORKFLOWS / "monitoring.yml")
    assert "rm -f data/metrics/public_metrics.json" in workflow
    assert "snapshot_valid" in workflow
    assert "steps.health.outputs.snapshot_valid == 'true'" in workflow
    assert "steps.session.outputs.run == 'true'" in workflow
    assert "floor-monitoring-state-writer" in workflow
    assert "floor-runtime-state-writer" not in workflow
    assert "monitoring_state.sh publish" in workflow
    assert "runtime_state.sh publish" not in workflow


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
    assert "git rebase --autostash" in workflow


def test_pages_restores_runtime_state_before_publication() -> None:
    workflow = _text(WORKFLOWS / "pages.yml")
    restore_pos = workflow.index("runtime_state.sh restore")
    publish_pos = workflow.index("python -m utils.pages_publish")
    assert restore_pos < publish_pos
    assert "runtime_state_source': 'github_release:runtime-state-v1'" in workflow


def test_history_compaction_is_manual_guarded_atomic_and_drift_safe() -> None:
    workflow = _text(WORKFLOWS / "manual_compact_git_history.yml")
    assert "workflow_dispatch:" in workflow
    assert "PURGE_GENERATED_DATA_HISTORY" in workflow
    assert "safe_after = info.market_close + timedelta(hours=1)" in workflow
    assert "--path data" in workflow
    assert "--invert-paths" in workflow
    assert "git clone --mirror" in workflow
    assert "refs/heads/*:refs/heads/*" in workflow
    assert "refs/tags/*:refs/tags/*" in workflow
    assert "open_prs" in workflow
    assert "protected_branches" in workflow
    assert "refs_pre_push.txt" in workflow
    assert "cmp -s" in workflow
    assert "push --dry-run --atomic --force --prune" in workflow
    assert "push --atomic --force --prune" in workflow
    restore_pos = workflow.index("runtime_state.sh restore")
    publish_pos = workflow.index("runtime_state.sh publish")
    assert restore_pos < publish_pos


def test_runtime_state_is_release_backed_checksum_verified_and_authoritative() -> None:
    script = _text(ROOT / "scripts" / "runtime_state.sh")
    assert 'TAG="${RUNTIME_STATE_TAG:-runtime-state-v1}"' in script
    assert 'MAX_MB="${RUNTIME_STATE_MAX_MB:-500}"' in script
    assert "gh release download" in script
    assert "gh release upload" in script
    assert "sha256sum -c" in script
    assert "for attempt in 1 2 3" in script
    assert "clear_runtime_state" in script
    assert script.index("clear_runtime_state\n  tar -xzf") > script.index("sha256sum -c")
    assert "member.issym() or member.islnk()" in script
    assert "validate_sqlite_state true" in script
    assert "validate_sqlite_state false" in script
    assert "PRAGMA wal_checkpoint(TRUNCATE)" in script
    assert "PRAGMA quick_check" in script
    assert "python -m floor.runtime_retention --data-dir data" in script
    assert "asset_bytes > max_bytes" in script
    assert "data/market" in script
    assert "data/persistence" in script
    assert "data/predictions" in script
    assert "data/training/reviews.jsonl" in script


def test_research_workflows_never_publish_authoritative_runtime_state() -> None:
    for filename in (
        "walk_forward_oos.yml",
        "capital_challenger_tournament.yml",
        "retrospective_replay.yml",
        "robust_range_v3.yml",
    ):
        workflow = _text(WORKFLOWS / filename)
        assert "runtime_state.sh publish" not in workflow
        assert "gh workflow run pages.yml" not in workflow


def test_every_authoritative_runtime_publish_restores_first() -> None:
    marker = "runtime_state.sh publish"
    for path in WORKFLOWS.glob("*.yml"):
        workflow = _text(path)
        if marker not in workflow:
            continue
        assert "runtime_state.sh restore" in workflow, path.name
        assert workflow.index("runtime_state.sh restore") < workflow.index(marker), path.name


def test_critical_market_workflows_skip_full_ledger_hydration_when_cache_exists() -> None:
    makefile = _text(ROOT / "Makefile")
    assert "init-db-schemas:" in makefile
    assert "hydrate-db:" in makefile
    for filename in ("intraday_engine.yml", "eod.yml"):
        workflow = _text(WORKFLOWS / filename)
        assert "make init-db-schemas" in workflow
        assert "make init-dbs" not in workflow
        assert "full ledger replay skipped" in workflow


def test_critical_runtime_installs_explicit_dependencies() -> None:
    for filename in ("intraday_engine.yml", "eod.yml", "monitoring.yml"):
        workflow = _text(WORKFLOWS / filename)
        assert 'python-version: "3.12"' in workflow
        assert 'pip install -e . "numpy>=2.0,<3"' in workflow


def test_eod_refuses_to_retrain_missing_frozen_weekly_model() -> None:
    workflow = _text(WORKFLOWS / "eod.yml")
    assert "Validate frozen Weekly challenger" in workflow
    assert "weekly_model_sha256" in workflow
    assert "bootstrap_weekly_model" not in workflow
    assert "performing one-time 2y bootstrap refresh" not in workflow


def test_intraday_repairs_stale_market_data_before_retrying_inference() -> None:
    workflow = _text(WORKFLOWS / "intraday_engine.yml")
    assert "utils.market_data_guard" in workflow
    assert "--max-stale-sessions 0" in workflow
    assert "--range 5d" in workflow
    assert "refreshing recent daily bars once" in workflow


def test_lightweight_state_scripts_are_syntactically_valid() -> None:
    for script in ("checkpoint_state.sh", "monitoring_state.sh"):
        subprocess.run(["bash", "-n", str(ROOT / "scripts" / script)], check=True)


def test_checkpoint_gates_do_not_download_full_runtime_state() -> None:
    intraday = _text(WORKFLOWS / "intraday_engine.yml")
    eod = _text(WORKFLOWS / "eod.yml")
    assert "Restore lightweight checkpoint state" in intraday
    assert "Restore lightweight checkpoint state" in eod
    assert "checkpoint_state.sh restore" in intraday
    assert "checkpoint_state.sh restore" in eod
    assert "validate-context" in intraday
    assert "validate-context" in eod


def test_pages_overlay_isolated_monitoring_state() -> None:
    workflow = _text(WORKFLOWS / "pages.yml")
    assert "monitoring_state.sh restore" in workflow
    assert "monitoring_state_source" in workflow


def test_runtime_state_publish_uses_optimistic_concurrency_token() -> None:
    script = _text(ROOT / "scripts" / "runtime_state.sh")
    assert "floor-runtime-state-restore-token.json" in script
    assert "utils.runtime_state_cas write-token" in script
    assert "utils.runtime_state_cas verify-parent" in script
    assert "generation" in script
    assert "parent_sha256" in script
    assert "restore token missing" in script


def test_retrain_execute_requires_explicit_human_authorization() -> None:
    workflow = _text(WORKFLOWS / "retrain_execute.yml")
    assert "workflow_run:" not in workflow
    assert "approve_recommended:" in workflow
    assert "tasks_for_auto_retrain_requested" in workflow
    assert "MANUAL_APPROVAL" in workflow
    assert "MANUAL_FORCE" in workflow
    assert "AUTHORIZED_PENDING_EXECUTION" in workflow
    assert 'control["execution"] = "COMPLETED"' in workflow
    assert "make init-db-schemas" in workflow
    assert "make init-dbs" not in workflow


def test_retrain_assessment_avoids_full_persistence_hydration() -> None:
    workflow = _text(WORKFLOWS / "retrain_assessment.yml")
    assert "make init-db-schemas" in workflow
    assert "make init-dbs" not in workflow
    assert "data/persistence/app.sqlite" not in workflow
    assert "Install modeling dependencies" in workflow
    assert "--universe config/universe.yaml" in workflow
    assert "--benchmark SPY" in workflow


def test_robust_range_strict_dominance_is_scoped_to_model_changes() -> None:
    workflow = _text(WORKFLOWS / "robust_range_v3.yml")
    assert "Determine whether strict promotion dominance is required" in workflow
    assert "steps.promotion_gate.outputs.require_dominance" in workflow
    assert "src/models/robust_range_v3.py" in workflow
    assert "src/models/train_classic_horizons.py" in workflow
    assert "src/features/*" in workflow
    assert "BASE_SHA: ${{ github.event.pull_request.base.sha }}" in workflow
    assert "HEAD_SHA: ${{ github.event.pull_request.head.sha }}" in workflow
    assert 'git diff --name-only "${BASE_SHA}" "${HEAD_SHA}"' in workflow
    assert "origin/${BASE_REF}...HEAD" not in workflow
    assert "src/forecasting/parity_models.py" not in workflow.split(
        "case \"$path\" in", 1
    )[1].split("esac", 1)[0]


def test_checkpoint_publish_declares_monotonic_runtime_frontier() -> None:
    runtime_script = _text(ROOT / "scripts" / "runtime_state.sh")
    intraday = _text(WORKFLOWS / "intraday_engine.yml")
    eod = _text(WORKFLOWS / "eod.yml")

    assert "resolve-frontier" in runtime_script
    assert "checkpoint_frontier" in runtime_script
    assert "RUNTIME_STATE_CHECKPOINT_AT" in runtime_script
    assert "RUNTIME_STATE_CHECKPOINT_AT" in intraday
    assert "RUNTIME_STATE_CHECKPOINT_EVENT" in intraday
    assert "RUNTIME_STATE_CHECKPOINT_AT" in eod
    assert "RUNTIME_STATE_CHECKPOINT_EVENT: CLOSE" in eod


def test_checkpoint_repair_is_explicit_audited_and_latest_only() -> None:
    intraday = _text(WORKFLOWS / "intraday_engine.yml")
    repair = _text(WORKFLOWS / "runtime_checkpoint_repair.yml")

    assert "repair_existing_checkpoint:" in intraday
    assert "--allow-existing-repair" in intraday
    assert "REPAIR_LATEST_COMPLETED_CHECKPOINT" in repair
    assert "request_superseded_by_" in repair
    assert "gh workflow run intraday_engine.yml" in repair
    assert "repair_existing_checkpoint=true" in repair
    assert "actions: write" in repair


def test_eod_generates_fresh_close_forecast_after_market_refresh() -> None:
    workflow = _text(WORKFLOWS / "eod.yml")
    refresh_pos = workflow.index("Refresh only recent daily market bars")
    forecast_pos = workflow.index("Generate canonical close forecast from refreshed daily bar")
    reconcile_pos = workflow.index("Reconcile matured predictions against realized bars")

    assert refresh_pos < forecast_pos < reconcile_pos
    assert "python -m floor.main run-cycle" in workflow
    assert "--event CLOSE" in workflow
    assert '--required-market-session "${{ needs.gate.outputs.required_market_session }}"' in workflow


def test_monitoring_has_runtime_completion_backstop() -> None:
    workflow = _text(WORKFLOWS / "monitoring.yml")

    assert "workflow_run:" in workflow
    assert 'workflows: ["intraday_engine", "eod"]' in workflow
    assert "types: [completed]" in workflow
    assert "branches: [main]" in workflow
    assert "Determine monitoring eligibility" in workflow
    assert "UPSTREAM_CONCLUSION: ${{ github.event.workflow_run.conclusion }}" in workflow
    assert "UPSTREAM_BRANCH: ${{ github.event.workflow_run.head_branch }}" in workflow
    assert '[ "$UPSTREAM_CONCLUSION" = "success" ]' in workflow
    assert '[ "$UPSTREAM_BRANCH" = "main" ]' in workflow
    assert '[ "$REF_NAME" = "main" ]' in workflow
    assert "steps.eligibility.outputs.run == 'true'" in workflow
