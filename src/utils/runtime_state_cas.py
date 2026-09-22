from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class RuntimeStateRevision:
    exists: bool
    sha256: str | None
    generation: int


@dataclass(frozen=True)
class RuntimeStateFrontier:
    checkpoint_at: str | None
    event: str | None


def _checkpoint_dt(value: str | None) -> datetime | None:
    if value in (None, ""):
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise RuntimeError(f"Runtime-state checkpoint must be timezone-aware: {value!r}")
    return parsed


def frontier_from_metadata(metadata_path: Path | None) -> RuntimeStateFrontier:
    if metadata_path is None or not metadata_path.exists():
        return RuntimeStateFrontier(None, None)
    payload = _load_metadata(metadata_path)
    raw = payload.get("checkpoint_frontier")
    if raw in (None, {}):
        return RuntimeStateFrontier(None, None)
    if not isinstance(raw, dict):
        raise RuntimeError("Runtime-state checkpoint_frontier must be an object")
    checkpoint_at = str(raw.get("checkpoint_at") or "").strip() or None
    event = str(raw.get("event") or "").strip() or None
    if checkpoint_at is None:
        if event is not None:
            raise RuntimeError("Runtime-state frontier event requires checkpoint_at")
        return RuntimeStateFrontier(None, None)
    _checkpoint_dt(checkpoint_at)
    if event is None:
        raise RuntimeError("Runtime-state frontier checkpoint requires event")
    return RuntimeStateFrontier(checkpoint_at, event)


def latest_marker_frontier(marker_dir: Path | None) -> RuntimeStateFrontier:
    if marker_dir is None or not marker_dir.exists():
        return RuntimeStateFrontier(None, None)
    latest: tuple[datetime, RuntimeStateFrontier] | None = None
    for path in sorted(marker_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Invalid checkpoint marker: {path}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"Checkpoint marker must be an object: {path}")
        checkpoint_at = str(payload.get("checkpoint_at") or "").strip() or None
        event = str(payload.get("event") or "").strip() or None
        if checkpoint_at is None:
            continue
        if event is None:
            raise RuntimeError(f"Checkpoint marker lacks event: {path}")
        parsed = _checkpoint_dt(checkpoint_at)
        assert parsed is not None
        frontier = RuntimeStateFrontier(checkpoint_at, event)
        if latest is None or parsed > latest[0]:
            latest = (parsed, frontier)
    return latest[1] if latest is not None else RuntimeStateFrontier(None, None)


def resolve_publish_frontier(
    *,
    parent_metadata_path: Path | None,
    marker_dir: Path | None,
    checkpoint_at: str | None,
    event: str | None,
) -> RuntimeStateFrontier:
    parent = frontier_from_metadata(parent_metadata_path)
    candidate = RuntimeStateFrontier(
        str(checkpoint_at or "").strip() or None,
        str(event or "").strip() or None,
    )
    if candidate.checkpoint_at is None:
        if candidate.event is not None:
            raise RuntimeError("Checkpoint event requires checkpoint_at")
        return parent
    if candidate.event is None:
        raise RuntimeError("Checkpoint publish requires event")

    candidate_dt = _checkpoint_dt(candidate.checkpoint_at)
    assert candidate_dt is not None
    floors = [parent, latest_marker_frontier(marker_dir)]
    for floor in floors:
        floor_dt = _checkpoint_dt(floor.checkpoint_at)
        if floor_dt is None:
            continue
        if candidate_dt < floor_dt:
            raise RuntimeError(
                "Runtime-state checkpoint regression refused: "
                f"candidate={candidate} frontier={floor}"
            )
        if candidate_dt == floor_dt and floor.event not in (None, candidate.event):
            raise RuntimeError(
                "Runtime-state checkpoint event mismatch at same timestamp: "
                f"candidate={candidate} frontier={floor}"
            )
    return candidate


def _read_checksum(path: Path) -> str:
    try:
        checksum = path.read_text(encoding="utf-8").strip().split()[0].lower()
    except (OSError, IndexError) as exc:
        raise RuntimeError(f"Invalid runtime-state checksum file: {path}") from exc
    if not SHA256_RE.fullmatch(checksum):
        raise RuntimeError(f"Invalid runtime-state sha256 in {path}: {checksum!r}")
    return checksum


def _load_metadata(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Invalid runtime-state metadata: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Runtime-state metadata must be a JSON object")
    return payload


def revision_from_remote(metadata_path: Path, checksum_path: Path) -> RuntimeStateRevision:
    checksum = _read_checksum(checksum_path)
    metadata = _load_metadata(metadata_path)
    metadata_sha = str(metadata.get("sha256") or "").lower()
    if metadata_sha != checksum:
        raise RuntimeError(
            "Runtime-state metadata/checksum mismatch: "
            f"metadata={metadata_sha!r} checksum={checksum!r}"
        )
    try:
        generation = int(metadata.get("generation", 0))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Runtime-state generation must be an integer") from exc
    if generation < 0:
        raise RuntimeError("Runtime-state generation must be >= 0")
    return RuntimeStateRevision(True, checksum, generation)


def missing_revision() -> RuntimeStateRevision:
    return RuntimeStateRevision(False, None, -1)


def write_token(token_path: Path, revision: RuntimeStateRevision) -> dict:
    token_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": 1, **asdict(revision)}
    token_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def load_token(token_path: Path) -> RuntimeStateRevision:
    try:
        payload = json.loads(token_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "Runtime-state publish refused: missing/invalid restore token; "
            "run runtime_state.sh restore first"
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Runtime-state restore token must be a JSON object")
    exists = bool(payload.get("exists", False))
    sha = payload.get("sha256")
    sha_value = str(sha).lower() if sha not in (None, "") else None
    if exists and (sha_value is None or not SHA256_RE.fullmatch(sha_value)):
        raise RuntimeError("Runtime-state restore token has invalid sha256")
    try:
        generation = int(payload.get("generation", -1))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Runtime-state restore token has invalid generation") from exc
    return RuntimeStateRevision(exists, sha_value, generation)


def verify_parent(token: RuntimeStateRevision, remote: RuntimeStateRevision) -> dict[str, object]:
    if token != remote:
        raise RuntimeError(
            "Runtime-state CAS refused stale publish: "
            f"restored={asdict(token)} remote={asdict(remote)}"
        )
    return {
        "parent_sha256": remote.sha256,
        "parent_generation": remote.generation,
        "next_generation": remote.generation + 1,
    }


def _remote_from_args(args: argparse.Namespace) -> RuntimeStateRevision:
    if args.remote_missing:
        return missing_revision()
    if not args.metadata or not args.checksum:
        raise RuntimeError("--metadata and --checksum are required unless --remote-missing")
    return revision_from_remote(Path(args.metadata), Path(args.checksum))


def main() -> int:
    parser = argparse.ArgumentParser(description="Runtime-state optimistic concurrency helper")
    sub = parser.add_subparsers(dest="cmd", required=True)

    write = sub.add_parser("write-token")
    write.add_argument("--token", required=True)
    write.add_argument("--metadata")
    write.add_argument("--checksum")
    write.add_argument("--remote-missing", action="store_true")

    verify = sub.add_parser("verify-parent")
    verify.add_argument("--token", required=True)
    verify.add_argument("--metadata")
    verify.add_argument("--checksum")
    verify.add_argument("--remote-missing", action="store_true")
    verify.add_argument("--format", choices=["json", "tsv"], default="json")

    frontier = sub.add_parser("resolve-frontier")
    frontier.add_argument("--metadata")
    frontier.add_argument("--marker-dir")
    frontier.add_argument("--checkpoint-at")
    frontier.add_argument("--event")
    frontier.add_argument("--format", choices=["json", "tsv"], default="json")

    args = parser.parse_args()
    try:
        if args.cmd == "write-token":
            revision = _remote_from_args(args)
            print(json.dumps(write_token(Path(args.token), revision), sort_keys=True))
            return 0

        if args.cmd == "resolve-frontier":
            resolved = resolve_publish_frontier(
                parent_metadata_path=Path(args.metadata) if args.metadata else None,
                marker_dir=Path(args.marker_dir) if args.marker_dir else None,
                checkpoint_at=args.checkpoint_at,
                event=args.event,
            )
            if args.format == "tsv":
                print(f"{resolved.checkpoint_at or '-'}\t{resolved.event or '-'}")
            else:
                print(json.dumps(asdict(resolved), sort_keys=True))
            return 0

        token = load_token(Path(args.token))
        remote = _remote_from_args(args)
        result = verify_parent(token, remote)
        if args.format == "tsv":
            print(
                f"{result['parent_generation']}\t{result['next_generation']}\t"
                f"{result['parent_sha256'] or '-'}"
            )
        else:
            print(json.dumps(result, sort_keys=True))
        return 0
    except RuntimeError as exc:
        print(f"runtime_state_cas_error={exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
