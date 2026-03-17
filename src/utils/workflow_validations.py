from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def _sqlite_count(db_path: Path, table: str) -> int:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        exists = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        if not exists:
            return 0
        row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    return int(row[0] if row else 0)


def _jsonl_rows(stream_dir: Path) -> int:
    total = 0
    if not stream_dir.exists():
        return 0
    for path in stream_dir.glob("*.jsonl"):
        total += sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    return total


def capture_baseline(db_path: Path, data_dir: Path, streams: list[str]) -> dict[str, int]:
    baseline: dict[str, int] = {}
    for stream in streams:
        baseline[f"sqlite_{stream}"] = _sqlite_count(db_path, stream)
        baseline[f"files_{stream}"] = _jsonl_rows(data_dir / stream)
    return baseline


def validate_deltas(db_path: Path, data_dir: Path, streams: list[str], baseline: dict[str, int], require_positive: set[str]) -> dict[str, int]:
    deltas: dict[str, int] = {}
    for stream in streams:
        sqlite_after = _sqlite_count(db_path, stream)
        files_after = _jsonl_rows(data_dir / stream)

        sqlite_before = int(baseline.get(f"sqlite_{stream}", 0))
        files_before = int(baseline.get(f"files_{stream}", 0))

        sqlite_delta = sqlite_after - sqlite_before
        files_delta = files_after - files_before
        deltas[f"delta_sqlite_{stream}"] = sqlite_delta
        deltas[f"delta_files_{stream}"] = files_delta

        if stream in require_positive and sqlite_delta <= 0:
            raise SystemExit(f"Expected positive sqlite delta for stream={stream}, got {sqlite_delta}")
        if stream in require_positive and files_delta <= 0:
            raise SystemExit(f"Expected positive artifact-file delta for stream={stream}, got {files_delta}")
        if sqlite_delta != files_delta:
            raise SystemExit(
                f"Mismatch for stream={stream}: sqlite_delta={sqlite_delta} files_delta={files_delta}"
            )
    return deltas


def validate_latest_payload(data_dir: Path, stream: str, required_fields: list[str]) -> dict:
    files = sorted((data_dir / stream).glob("*.jsonl"))
    if not files:
        raise SystemExit(f"No files found for stream={stream} in {(data_dir / stream)}")

    sample = files[-1]
    lines = [line.strip() for line in sample.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        raise SystemExit(f"Empty artifact file: {sample}")

    payload = json.loads(lines[-1])
    for field in required_fields:
        if str(payload.get(field, "")).strip() == "":
            raise SystemExit(f"Missing required field '{field}' in file {sample}")

    return {"sample_file": str(sample), "rows": len(lines), "payload": payload}



def validate_json_file(path: Path, required_fields: list[str]) -> dict:
    if not path.exists():
        raise SystemExit(f"Required file missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    for field in required_fields:
        if str(payload.get(field, "")).strip() == "":
            raise SystemExit(f"Missing required field '{field}' in json file {path}")
    return payload

def _write_outputs(path: Path | None, payload: dict[str, int]) -> None:
    if path is None:
        return
    with path.open("a", encoding="utf-8") as fh:
        for key, value in payload.items():
            fh.write(f"{key}={value}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Reusable workflow validations for sqlite + jsonl artifacts")
    sub = parser.add_subparsers(dest="cmd", required=True)

    baseline_p = sub.add_parser("capture-baseline")
    baseline_p.add_argument("--db", required=True)
    baseline_p.add_argument("--data-dir", required=True)
    baseline_p.add_argument("--streams", default="predictions,signals")
    baseline_p.add_argument("--output-file", default=None)

    validate_p = sub.add_parser("validate-deltas")
    validate_p.add_argument("--db", required=True)
    validate_p.add_argument("--data-dir", required=True)
    validate_p.add_argument("--streams", default="predictions,signals")
    validate_p.add_argument("--require-positive", default="predictions,signals")
    validate_p.add_argument("--baseline-json", required=True)
    validate_p.add_argument("--output-file", default=None)

    artifact_p = sub.add_parser("validate-latest-payload")
    artifact_p.add_argument("--data-dir", required=True)
    artifact_p.add_argument("--stream", default="predictions")
    artifact_p.add_argument("--required-fields", default="model_version")

    json_p = sub.add_parser("validate-json-file")
    json_p.add_argument("--path", required=True)
    json_p.add_argument("--required-fields", default="model_name,version")

    args = parser.parse_args()

    if args.cmd == "capture-baseline":
        streams = [s.strip() for s in args.streams.split(",") if s.strip()]
        payload = capture_baseline(Path(args.db), Path(args.data_dir), streams)
        _write_outputs(Path(args.output_file) if args.output_file else None, payload)
        print(json.dumps(payload, ensure_ascii=False))
        return

    if args.cmd == "validate-deltas":
        streams = [s.strip() for s in args.streams.split(",") if s.strip()]
        require_positive = {s.strip() for s in args.require_positive.split(",") if s.strip()}
        baseline = json.loads(Path(args.baseline_json).read_text(encoding="utf-8"))
        payload = validate_deltas(Path(args.db), Path(args.data_dir), streams, baseline, require_positive)
        _write_outputs(Path(args.output_file) if args.output_file else None, payload)
        print(json.dumps(payload, ensure_ascii=False))
        return

    if args.cmd == "validate-latest-payload":
        required_fields = [s.strip() for s in args.required_fields.split(",") if s.strip()]
        out = validate_latest_payload(Path(args.data_dir), args.stream, required_fields)
        print(
            json.dumps(
                {
                    "sample_file": out["sample_file"],
                    "rows": out["rows"],
                    "required_fields": required_fields,
                },
                ensure_ascii=False,
            )
        )
        return

    required_fields = [s.strip() for s in args.required_fields.split(",") if s.strip()]
    payload = validate_json_file(Path(args.path), required_fields)
    print(json.dumps({"path": args.path, "required_fields": required_fields, "keys": sorted(payload.keys())}, ensure_ascii=False))


if __name__ == "__main__":
    main()
