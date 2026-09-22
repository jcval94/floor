from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class RuntimeStateRevision:
    exists: bool
    sha256: str | None
    generation: int


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

    args = parser.parse_args()
    try:
        if args.cmd == "write-token":
            revision = _remote_from_args(args)
            print(json.dumps(write_token(Path(args.token), revision), sort_keys=True))
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
