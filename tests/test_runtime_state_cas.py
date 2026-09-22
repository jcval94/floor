from __future__ import annotations

import json
from pathlib import Path

import pytest

from utils.runtime_state_cas import (
    RuntimeStateFrontier,
    RuntimeStateRevision,
    load_token,
    missing_revision,
    revision_from_remote,
    resolve_publish_frontier,
    verify_parent,
    write_token,
)


def _remote(tmp_path: Path, sha: str, generation: int | None = None) -> RuntimeStateRevision:
    checksum = tmp_path / "state.sha256"
    metadata = tmp_path / "state.metadata.json"
    checksum.write_text(f"{sha}  floor-runtime-state.tar.gz\n", encoding="utf-8")
    payload = {"schema_version": 2, "sha256": sha}
    if generation is not None:
        payload["generation"] = generation
    metadata.write_text(json.dumps(payload), encoding="utf-8")
    return revision_from_remote(metadata, checksum)


def test_legacy_metadata_defaults_to_generation_zero(tmp_path: Path) -> None:
    sha = "a" * 64
    revision = _remote(tmp_path, sha)
    assert revision == RuntimeStateRevision(True, sha, 0)


def test_matching_parent_advances_generation(tmp_path: Path) -> None:
    sha = "b" * 64
    remote = _remote(tmp_path, sha, generation=7)
    token_path = tmp_path / "token.json"
    write_token(token_path, remote)
    result = verify_parent(load_token(token_path), remote)
    assert result == {
        "parent_sha256": sha,
        "parent_generation": 7,
        "next_generation": 8,
    }


def test_stale_parent_is_rejected(tmp_path: Path) -> None:
    token_path = tmp_path / "token.json"
    write_token(token_path, RuntimeStateRevision(True, "c" * 64, 3))
    with pytest.raises(RuntimeError, match="stale publish"):
        verify_parent(load_token(token_path), RuntimeStateRevision(True, "d" * 64, 4))


def test_missing_genesis_can_publish_only_if_remote_is_still_missing(tmp_path: Path) -> None:
    token_path = tmp_path / "token.json"
    write_token(token_path, missing_revision())
    assert verify_parent(load_token(token_path), missing_revision())["next_generation"] == 0
    with pytest.raises(RuntimeError, match="stale publish"):
        verify_parent(load_token(token_path), RuntimeStateRevision(True, "e" * 64, 0))


def test_metadata_checksum_mismatch_is_rejected(tmp_path: Path) -> None:
    checksum = tmp_path / "state.sha256"
    metadata = tmp_path / "state.metadata.json"
    checksum.write_text(f"{'f' * 64}  floor-runtime-state.tar.gz\n", encoding="utf-8")
    metadata.write_text(
        json.dumps({"schema_version": 2, "sha256": "0" * 64}),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="metadata/checksum mismatch"):
        revision_from_remote(metadata, checksum)


def _metadata_with_frontier(tmp_path: Path, checkpoint_at: str, event: str) -> Path:
    path = tmp_path / "parent.metadata.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 4,
                "checkpoint_frontier": {
                    "checkpoint_at": checkpoint_at,
                    "event": event,
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def _marker(tmp_path: Path, event: str, checkpoint_at: str) -> Path:
    marker_dir = tmp_path / "markers"
    marker_dir.mkdir(parents=True, exist_ok=True)
    path = marker_dir / f"{event}.json"
    path.write_text(
        json.dumps({"event": event, "checkpoint_at": checkpoint_at}),
        encoding="utf-8",
    )
    return marker_dir


def test_checkpoint_frontier_refuses_regression_against_parent_metadata(tmp_path: Path) -> None:
    metadata = _metadata_with_frontier(
        tmp_path, "2026-09-22T11:30:00-04:00", "OPEN_PLUS_2H"
    )
    with pytest.raises(RuntimeError, match="checkpoint regression"):
        resolve_publish_frontier(
            parent_metadata_path=metadata,
            marker_dir=None,
            checkpoint_at="2026-09-22T09:30:00-04:00",
            event="OPEN",
        )


def test_checkpoint_frontier_refuses_regression_against_completed_marker(tmp_path: Path) -> None:
    markers = _marker(
        tmp_path, "OPEN_PLUS_2H", "2026-09-22T11:30:00-04:00"
    )
    with pytest.raises(RuntimeError, match="checkpoint regression"):
        resolve_publish_frontier(
            parent_metadata_path=None,
            marker_dir=markers,
            checkpoint_at="2026-09-22T09:30:00-04:00",
            event="OPEN",
        )


def test_checkpoint_frontier_allows_idempotent_repair_of_latest_checkpoint(tmp_path: Path) -> None:
    metadata = _metadata_with_frontier(
        tmp_path, "2026-09-22T11:30:00-04:00", "OPEN_PLUS_2H"
    )
    markers = _marker(
        tmp_path, "OPEN_PLUS_2H", "2026-09-22T11:30:00-04:00"
    )
    resolved = resolve_publish_frontier(
        parent_metadata_path=metadata,
        marker_dir=markers,
        checkpoint_at="2026-09-22T11:30:00-04:00",
        event="OPEN_PLUS_2H",
    )
    assert resolved == RuntimeStateFrontier(
        "2026-09-22T11:30:00-04:00", "OPEN_PLUS_2H"
    )


def test_non_checkpoint_publisher_inherits_parent_frontier(tmp_path: Path) -> None:
    metadata = _metadata_with_frontier(
        tmp_path, "2026-09-22T11:30:00-04:00", "OPEN_PLUS_2H"
    )
    resolved = resolve_publish_frontier(
        parent_metadata_path=metadata,
        marker_dir=None,
        checkpoint_at=None,
        event=None,
    )
    assert resolved == RuntimeStateFrontier(
        "2026-09-22T11:30:00-04:00", "OPEN_PLUS_2H"
    )
