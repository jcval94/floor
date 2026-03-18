from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


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


def _iter_jsonl_payloads(stream_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not stream_dir.exists():
        return rows
    for path in sorted(stream_dir.glob("*.jsonl")):
        for idx, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            payload["__file__"] = str(path)
            payload["__line__"] = idx
            rows.append(payload)
    return rows


def _sample_rows(rows: list[dict[str, Any]], sample_limit: int) -> str:
    sample = []
    for row in rows[:sample_limit]:
        sample.append(
            {
                "symbol": row.get("symbol"),
                "horizon": row.get("horizon"),
                "action": row.get("action"),
                "file": row.get("__file__"),
                "line": row.get("__line__"),
                "missing_fields": row.get("__missing_fields__"),
                "validation_mode": row.get("__validation_mode__"),
            }
        )
    return json.dumps(sample, ensure_ascii=False)


def _fail_false_value(message: str, rows: list[dict[str, Any]], sample_limit: int) -> None:
    raise SystemExit(f"::error::valor falso: {message}. sample_rows={_sample_rows(rows, sample_limit)}")


def _parse_as_of(value: Any) -> datetime | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    normalized = raw.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _latest_batch_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str | None]:
    dated = []
    for row in rows:
        parsed = _parse_as_of(row.get("as_of"))
        if parsed is not None:
            dated.append((parsed, row))
    if not dated:
        return rows, None
    latest_dt = max(item[0] for item in dated)
    selected = [row for parsed, row in dated if parsed == latest_dt]
    return selected, latest_dt.isoformat()


def _prediction_quality_diagnostics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_horizon: dict[str, int] = {}
    null_action_by_horizon: dict[str, int] = {}
    schema_counts = {
        "legacy_floor_ceiling": 0,
        "per_horizon_floor_ceiling": 0,
    }

    for row in rows:
        horizon = str(row.get("horizon", "")).strip().lower() or "<missing>"
        by_horizon[horizon] = by_horizon.get(horizon, 0) + 1
        if row.get("action") is None or str(row.get("action", "")).strip() == "":
            null_action_by_horizon[horizon] = null_action_by_horizon.get(horizon, 0) + 1

        if all(str(row.get(k, "")).strip() != "" for k in ("floor_value", "ceiling_value")):
            schema_counts["legacy_floor_ceiling"] += 1
        if all(str(row.get(k, "")).strip() != "" for k in ("floor_d1", "ceiling_d1", "floor_w1", "ceiling_w1", "floor_q1", "ceiling_q1")):
            schema_counts["per_horizon_floor_ceiling"] += 1

    return {
        "rows_total": len(rows),
        "rows_by_horizon": by_horizon,
        "rows_with_null_action_by_horizon": null_action_by_horizon,
        "schema_presence": schema_counts,
    }


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def validate_prediction_quality(
    data_dir: Path,
    stream: str,
    max_m3_blocked_ratio: float,
    min_action_consistency_ratio: float,
    action_return_tolerance: float,
    sample_limit: int,
    evaluation_scope: str = "latest_batch",
) -> dict[str, Any]:
    rows = _iter_jsonl_payloads(data_dir / stream)
    if not rows:
        raise SystemExit(f"No prediction rows found in {(data_dir / stream)}")

    selected_rows = rows
    latest_as_of = None
    if evaluation_scope == "latest_batch":
        selected_rows, latest_as_of = _latest_batch_rows(rows)

    diagnostics = _prediction_quality_diagnostics(selected_rows)
    print(
        "::notice::prediction_quality diagnostics="
        + json.dumps(
            {
                **diagnostics,
                "rows_total_raw": len(rows),
                "rows_total_evaluated": len(selected_rows),
                "evaluation_scope": evaluation_scope,
                "latest_as_of": latest_as_of,
            },
            ensure_ascii=False,
        )
    )

    rows = selected_rows

    invalid_band_rows = [
        row
        for row in rows
        if any(
            (
                (floor_val := _to_float(row.get(f"floor_{hz}"))) is not None
                and (ceiling_val := _to_float(row.get(f"ceiling_{hz}"))) is not None
                and floor_val >= ceiling_val
            )
            for hz in ("d1", "w1", "q1")
        )
        or (
            row.get("horizon") in {"d1", "w1", "q1"}
            and (floor_value := _to_float(row.get("floor_value"))) is not None
            and (ceiling_value := _to_float(row.get("ceiling_value"))) is not None
            and floor_value >= ceiling_value
        )
    ]
    if invalid_band_rows:
        _fail_false_value("floor_value debe ser menor que ceiling_value en d1/w1/q1", invalid_band_rows, sample_limit)

    required_by_horizon = {
        "d1": ["floor_d1", "ceiling_d1", "expected_return_d1"],
        "w1": ["floor_w1", "ceiling_w1", "expected_return_w1"],
        "q1": ["floor_q1", "ceiling_q1", "expected_return_q1"],
        "m3": ["m3_status", "m3_block_reason"],
    }
    # compatibility with legacy per-horizon payloads.
    legacy_required = ["floor_value", "ceiling_value", "model_version"]

    missing_critical_rows: list[dict[str, Any]] = []
    for row in rows:
        horizon = str(row.get("horizon", "")).strip().lower()
        if horizon in {"d1", "w1", "q1"}:
            missing_fields = [field for field in legacy_required if str(row.get(field, "")).strip() == ""]
            if missing_fields:
                row["__missing_fields__"] = missing_fields
                row["__validation_mode__"] = "legacy_short_horizon"
                missing_critical_rows.append(row)
            continue
        required_fields = required_by_horizon.get(horizon)
        if required_fields:
            missing_fields = [field for field in required_fields if str(row.get(field, "")).strip() == ""]
            if missing_fields:
                row["__missing_fields__"] = missing_fields
                row["__validation_mode__"] = f"per_horizon:{horizon}"
                missing_critical_rows.append(row)

    if missing_critical_rows:
        cause_counts: dict[str, int] = {}
        for row in missing_critical_rows:
            for field in row.get("__missing_fields__", []):
                cause_counts[field] = cause_counts.get(field, 0) + 1
        message = (
            "campos críticos nulos por horizonte "
            f"(causas={json.dumps(cause_counts, ensure_ascii=False)}, "
            f"diagnostics={json.dumps(diagnostics, ensure_ascii=False)})"
        )
        _fail_false_value(message, missing_critical_rows, sample_limit)

    m3_rows = [row for row in rows if str(row.get("horizon", "")).strip().lower() == "m3"]
    m3_total = sum(1 for row in m3_rows if str(row.get("m3_status", "")).strip() != "")
    m3_blocked = sum(1 for row in m3_rows if str(row.get("m3_status", "")).lower() == "blocked")
    blocked_ratio = (m3_blocked / m3_total) if m3_total else 0.0
    if blocked_ratio > max_m3_blocked_ratio:
        _fail_false_value(
            f"ratio m3_status=blocked {blocked_ratio:.4f} supera umbral {max_m3_blocked_ratio:.4f}",
            [row for row in m3_rows if str(row.get("m3_status", "")).lower() == "blocked"],
            sample_limit,
        )

    actionable = []
    consistent = 0
    for row in rows:
        action = str(row.get("action", "")).upper().strip()
        if action not in {"BUY", "SELL", "HOLD"}:
            continue
        ret = row.get("expected_return")
        if ret is None:
            horizon = str(row.get("horizon", "")).strip()
            if horizon in {"d1", "w1", "q1", "m3"}:
                ret = row.get(f"expected_return_{horizon}")
            else:
                ret = row.get("expected_return_d1")
        if ret is None:
            continue
        parsed_ret = _to_float(ret)
        if parsed_ret is None:
            continue
        val = parsed_ret
        actionable.append(row)
        if (action == "BUY" and val >= action_return_tolerance) or (
            action == "SELL" and val <= -action_return_tolerance
        ) or (action == "HOLD" and abs(val) <= action_return_tolerance):
            consistent += 1

    action_consistency_ratio = (consistent / len(actionable)) if actionable else 1.0
    if action_consistency_ratio < min_action_consistency_ratio:
        inconsistent = []
        for row in actionable:
            action = str(row.get("action", "")).upper().strip()
            horizon = str(row.get("horizon", "")).strip()
            ret = row.get("expected_return")
            if ret is None and horizon in {"d1", "w1", "q1", "m3"}:
                ret = row.get(f"expected_return_{horizon}")
            if ret is None:
                ret = row.get("expected_return_d1")
            parsed_ret = _to_float(ret)
            if parsed_ret is None:
                continue
            val = parsed_ret
            ok = (action == "BUY" and val >= action_return_tolerance) or (
                action == "SELL" and val <= -action_return_tolerance
            ) or (action == "HOLD" and abs(val) <= action_return_tolerance)
            if not ok:
                inconsistent.append(row)
        _fail_false_value(
            f"consistencia acción/retorno {action_consistency_ratio:.4f} debajo de mínimo {min_action_consistency_ratio:.4f}",
            inconsistent,
            sample_limit,
        )

    return {
        "rows": len(rows),
        "m3_total": m3_total,
        "m3_blocked": m3_blocked,
        "m3_blocked_ratio": round(blocked_ratio, 6),
        "actionable_rows": len(actionable),
        "action_consistency_ratio": round(action_consistency_ratio, 6),
    }

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

    pred_quality_p = sub.add_parser("validate-prediction-quality")
    pred_quality_p.add_argument("--data-dir", required=True)
    pred_quality_p.add_argument("--stream", default="predictions")
    pred_quality_p.add_argument("--max-m3-blocked-ratio", type=float, default=0.4)
    pred_quality_p.add_argument("--min-action-consistency-ratio", type=float, default=0.7)
    pred_quality_p.add_argument("--action-return-tolerance", type=float, default=0.0)
    pred_quality_p.add_argument("--sample-limit", type=int, default=5)
    pred_quality_p.add_argument("--evaluation-scope", choices=["latest_batch", "all_rows"], default="latest_batch")

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

    if args.cmd == "validate-prediction-quality":
        payload = validate_prediction_quality(
            data_dir=Path(args.data_dir),
            stream=args.stream,
            max_m3_blocked_ratio=args.max_m3_blocked_ratio,
            min_action_consistency_ratio=args.min_action_consistency_ratio,
            action_return_tolerance=args.action_return_tolerance,
            sample_limit=args.sample_limit,
            evaluation_scope=args.evaluation_scope,
        )
        print(json.dumps(payload, ensure_ascii=False))
        return

    required_fields = [s.strip() for s in args.required_fields.split(",") if s.strip()]
    payload = validate_json_file(Path(args.path), required_fields)
    print(json.dumps({"path": args.path, "required_fields": required_fields, "keys": sorted(payload.keys())}, ensure_ascii=False))


if __name__ == "__main__":
    main()
