from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_INPUT_SNAPSHOT_RE = re.compile(r"input_snapshot_id=([0-9a-f]{64})")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(
                json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                + "\n"
            )


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone()
    return row is not None


def _batch_rows(db_path: Path, table: str, batch_id: str) -> list[dict[str, Any]]:
    if not db_path.exists():
        raise FileNotFoundError(f"persistence DB missing: {db_path}")

    with sqlite3.connect(db_path) as conn:
        if not _table_exists(conn, table):
            return []
        columns = {
            str(row[1])
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if "batch_id" not in columns or "payload_json" not in columns:
            return []
        rows = conn.execute(
            f"SELECT payload_json FROM {table} WHERE batch_id=? ORDER BY id ASC",
            (batch_id,),
        ).fetchall()

    out: list[dict[str, Any]] = []
    for (raw_payload,) in rows:
        payload = json.loads(str(raw_payload))
        if not isinstance(payload, dict):
            raise ValueError(f"{table} payload_json must be an object")
        if str(payload.get("batch_id") or "") != batch_id:
            raise ValueError(f"{table} payload batch_id mismatch")
        out.append({str(key): value for key, value in payload.items()})
    return out


def _copy_if_exists(source: Path, destination: Path) -> bool:
    if not source.is_file():
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return True


def _copy_run_logs(data_dir: Path, output_dir: Path, event: str) -> list[Path]:
    log_root = data_dir / "logs"
    copied: list[Path] = []
    candidates = [
        log_root / "baseline.env",
        log_root / "intraday_baseline.json",
        *sorted(log_root.glob(f"intraday_engine_{event}_attempt*.log")),
    ]
    for source in candidates:
        destination = output_dir / "logs" / source.name
        if _copy_if_exists(source, destination):
            copied.append(destination)
    return copied


def _detect_input_snapshot_id(log_paths: list[Path]) -> str | None:
    found: str | None = None
    for path in sorted(log_paths):
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in _INPUT_SNAPSHOT_RE.finditer(text):
            found = match.group(1)
    return found


def _find_input_snapshot_by_batch(data_dir: Path, batch_id: str) -> tuple[str, Path] | None:
    root = data_dir / "snapshots" / "input_snapshots"
    if not root.exists():
        return None
    for path in sorted(root.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and str(payload.get("first_batch_id") or "") == batch_id:
            return path.stem, path
    return None


def _file_inventory(output_dir: Path) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for path in sorted(item for item in output_dir.rglob("*") if item.is_file()):
        relative = path.relative_to(output_dir).as_posix()
        if relative in {"manifest.json", "SHA256SUMS"}:
            continue
        files.append(
            {
                "path": relative,
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    return files


def build_intraday_audit(
    *,
    data_dir: Path,
    output_dir: Path,
    batch_id: str,
    event: str,
    session_day: str,
    checkpoint_at: str,
    required_market_session: str,
    run_id: str,
    source_sha: str,
) -> dict[str, Any]:
    expected_batch_id = f"{session_day}:{event}"
    if batch_id != expected_batch_id:
        raise ValueError(
            f"batch_id mismatch expected={expected_batch_id} actual={batch_id}"
        )

    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    db_path = data_dir / "persistence" / "app.sqlite"
    prediction_rows = _batch_rows(db_path, "predictions", batch_id)
    signal_rows = _batch_rows(db_path, "signals", batch_id)
    _write_jsonl(output_dir / "evidence" / "predictions.jsonl", prediction_rows)
    _write_jsonl(output_dir / "evidence" / "signals.jsonl", signal_rows)

    log_paths = _copy_run_logs(data_dir, output_dir, event)

    workflow_marker = (
        data_dir
        / "snapshots"
        / "workflow_runs"
        / f"intraday_{session_day}_{event}.json"
    )
    if not _copy_if_exists(
        workflow_marker,
        output_dir / "snapshots" / "workflow_run.json",
    ):
        raise FileNotFoundError(f"workflow marker missing: {workflow_marker}")

    input_snapshot_id = _detect_input_snapshot_id(log_paths)
    input_snapshot_path: Path | None = None
    if input_snapshot_id:
        candidate = (
            data_dir
            / "snapshots"
            / "input_snapshots"
            / f"{input_snapshot_id}.json"
        )
        if candidate.is_file():
            input_snapshot_path = candidate
    if input_snapshot_path is None:
        fallback = _find_input_snapshot_by_batch(data_dir, batch_id)
        if fallback is not None:
            input_snapshot_id, input_snapshot_path = fallback
    if input_snapshot_path is not None:
        _copy_if_exists(
            input_snapshot_path,
            output_dir / "snapshots" / "input_snapshot.json",
        )

    _copy_if_exists(
        data_dir / "reports" / "dashboard.json",
        output_dir / "reports" / "dashboard.json",
    )

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "artifact_type": "intraday_run_audit",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repository": os.getenv("GITHUB_REPOSITORY"),
        "workflow": os.getenv("GITHUB_WORKFLOW", "intraday_engine"),
        "run_id": str(run_id),
        "source_sha": source_sha,
        "event": event,
        "session_day": session_day,
        "checkpoint_at": checkpoint_at,
        "required_market_session": required_market_session,
        "batch_id": batch_id,
        "input_snapshot_id": input_snapshot_id,
        "prediction_rows": len(prediction_rows),
        "signal_rows": len(signal_rows),
        "orders_expected": False,
        "orders_reason": "canonical_intraday_order_generation_disabled",
        "durable_authority": "rolling_runtime_state_jsonl",
        "batch_extract_source": "reconstructable_sqlite_cache",
    }
    manifest["files"] = _file_inventory(output_dir)
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    checksum_lines = []
    for path in sorted(item for item in output_dir.rglob("*") if item.is_file()):
        if path.name == "SHA256SUMS":
            continue
        checksum_lines.append(
            f"{_sha256(path)}  {path.relative_to(output_dir).as_posix()}"
        )
    (output_dir / "SHA256SUMS").write_text(
        "\n".join(checksum_lines) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a compact immutable evidence bundle for one intraday run"
    )
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--event", required=True)
    parser.add_argument("--session-day", required=True)
    parser.add_argument("--checkpoint-at", required=True)
    parser.add_argument("--required-market-session", required=True)
    parser.add_argument("--run-id", default=os.getenv("GITHUB_RUN_ID", "local"))
    parser.add_argument("--source-sha", default=os.getenv("GITHUB_SHA", "unknown"))
    args = parser.parse_args()

    manifest = build_intraday_audit(
        data_dir=Path(args.data_dir),
        output_dir=Path(args.output_dir),
        batch_id=args.batch_id,
        event=args.event,
        session_day=args.session_day,
        checkpoint_at=args.checkpoint_at,
        required_market_session=args.required_market_session,
        run_id=args.run_id,
        source_sha=args.source_sha,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
