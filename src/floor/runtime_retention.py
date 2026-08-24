from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from floor.prediction_reconciliation import prediction_key


@dataclass(frozen=True)
class RuntimeRetentionPolicy:
    prediction_days: int = 120
    operational_ledger_days: int = 180
    reconciliation_days: int = 730
    training_audit_days: int = 730
    market_days: int = 1460
    snapshot_days: int = 90
    keep_latest_files: int = 5


def _parse_dt(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _load_jsonl_strict(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            text = raw.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Malformed runtime JSONL path={path} line={line_number}: {exc}"
                ) from exc
            if not isinstance(payload, dict):
                raise ValueError(
                    f"Runtime JSONL row must be an object path={path} line={line_number}"
                )
            rows.append({str(key): value for key, value in payload.items()})
    return rows


def _write_jsonl_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".retention.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp.replace(path)


def _reconciled_prediction_keys(data_dir: Path) -> set[str]:
    keys: set[str] = set()
    directory = data_dir / "predictions" / "reconciliations"
    paths = sorted(directory.glob("*.jsonl")) if directory.exists() else []
    for path in paths:
        for row in _load_jsonl_strict(path):
            key = str(row.get("prediction_key") or "").strip()
            if key:
                keys.add(key)
    return keys


def _prune_predictions(
    data_dir: Path,
    *,
    now: datetime,
    policy: RuntimeRetentionPolicy,
) -> dict[str, int]:
    directory = data_dir / "predictions"
    reconciled = _reconciled_prediction_keys(data_dir)
    cutoff = now - timedelta(days=policy.prediction_days)
    kept = removed = old_unresolved_kept = malformed_time_kept = 0

    paths = sorted(directory.glob("*.jsonl")) if directory.exists() else []
    for path in paths:
        rows = _load_jsonl_strict(path)
        output: list[dict[str, Any]] = []
        for row in rows:
            as_of = _parse_dt(row.get("as_of"))
            if as_of is None:
                output.append(row)
                malformed_time_kept += 1
                continue
            if as_of >= cutoff:
                output.append(row)
                continue

            key = str(row.get("prediction_key") or "").strip()
            if not key:
                key = prediction_key(row)
            if key and key in reconciled:
                removed += 1
                continue

            output.append(row)
            old_unresolved_kept += 1

        kept += len(output)
        if output != rows:
            _write_jsonl_atomic(path, output)

    return {
        "kept": kept,
        "removed_resolved_old": removed,
        "old_unresolved_kept": old_unresolved_kept,
        "malformed_time_kept": malformed_time_kept,
    }


def _prune_jsonl_path(
    path: Path,
    *,
    timestamp_fields: tuple[str, ...],
    cutoff: datetime,
) -> dict[str, int]:
    if not path.exists():
        return {"kept": 0, "removed": 0, "malformed_time_kept": 0}
    rows = _load_jsonl_strict(path)
    output: list[dict[str, Any]] = []
    removed = malformed_time_kept = 0
    for row in rows:
        timestamp = None
        for field in timestamp_fields:
            timestamp = _parse_dt(row.get(field))
            if timestamp is not None:
                break
        if timestamp is None:
            output.append(row)
            malformed_time_kept += 1
        elif timestamp >= cutoff:
            output.append(row)
        else:
            removed += 1
    if output != rows:
        _write_jsonl_atomic(path, output)
    return {
        "kept": len(output),
        "removed": removed,
        "malformed_time_kept": malformed_time_kept,
    }


def _prune_jsonl_tree(
    root: Path,
    *,
    timestamp_fields: tuple[str, ...],
    cutoff: datetime,
) -> dict[str, int]:
    kept = removed = malformed_time_kept = 0
    paths = sorted(root.rglob("*.jsonl")) if root.exists() else []
    for path in paths:
        result = _prune_jsonl_path(
            path,
            timestamp_fields=timestamp_fields,
            cutoff=cutoff,
        )
        kept += result["kept"]
        removed += result["removed"]
        malformed_time_kept += result["malformed_time_kept"]
    return {
        "kept": kept,
        "removed": removed,
        "malformed_time_kept": malformed_time_kept,
    }


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone()
    return row is not None


def _prune_sqlite_table(
    conn: sqlite3.Connection,
    *,
    table: str,
    timestamp_field: str,
    cutoff: datetime,
) -> int:
    if not _table_exists(conn, table):
        return 0
    rows = conn.execute(f"SELECT id, {timestamp_field} FROM {table}").fetchall()
    remove_ids = [
        int(row_id)
        for row_id, raw_timestamp in rows
        if (parsed := _parse_dt(raw_timestamp)) is not None and parsed < cutoff
    ]
    if remove_ids:
        conn.executemany(
            f"DELETE FROM {table} WHERE id=?",
            [(row_id,) for row_id in remove_ids],
        )
    return len(remove_ids)


def _compact_app_db(
    path: Path,
    *,
    now: datetime,
    policy: RuntimeRetentionPolicy,
) -> dict[str, int]:
    if not path.exists():
        return {}
    cutoffs = {
        "predictions": ("as_of", now - timedelta(days=policy.prediction_days)),
        "signals": ("as_of", now - timedelta(days=policy.operational_ledger_days)),
        "orders": ("as_of", now - timedelta(days=policy.operational_ledger_days)),
        "training_reviews": (
            "as_of",
            now - timedelta(days=policy.training_audit_days),
        ),
        "model_competition_results": (
            "as_of",
            now - timedelta(days=policy.training_audit_days),
        ),
        "model_training_cycles": (
            "as_of",
            now - timedelta(days=policy.training_audit_days),
        ),
        "prediction_reconciliations": (
            "resolved_at",
            now - timedelta(days=policy.reconciliation_days),
        ),
    }
    removed: dict[str, int] = {}
    with sqlite3.connect(path) as conn:
        for table, (field, cutoff) in cutoffs.items():
            removed[table] = _prune_sqlite_table(
                conn,
                table=table,
                timestamp_field=field,
                cutoff=cutoff,
            )
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        quick = conn.execute("PRAGMA quick_check").fetchall()
        if not quick or any(str(row[0]).lower() != "ok" for row in quick):
            raise RuntimeError(
                f"SQLite quick_check failed after retention: {quick[:10]}"
            )
        conn.execute("VACUUM")
    return removed


def _compact_market_db(
    path: Path,
    *,
    now: datetime,
    policy: RuntimeRetentionPolicy,
) -> int:
    if not path.exists():
        return 0
    cutoff_date = (now - timedelta(days=policy.market_days)).date().isoformat()
    with sqlite3.connect(path) as conn:
        if not _table_exists(conn, "daily_bars"):
            return 0
        before_row = conn.execute("SELECT COUNT(*) FROM daily_bars").fetchone()
        before = int(before_row[0]) if before_row else 0
        conn.execute(
            "DELETE FROM daily_bars WHERE substr(ts_utc, 1, 10) < ?",
            (cutoff_date,),
        )
        after_row = conn.execute("SELECT COUNT(*) FROM daily_bars").fetchone()
        after = int(after_row[0]) if after_row else 0
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        quick = conn.execute("PRAGMA quick_check").fetchall()
        if not quick or any(str(row[0]).lower() != "ok" for row in quick):
            raise RuntimeError(
                f"Market SQLite quick_check failed after retention: {quick[:10]}"
            )
        conn.execute("VACUUM")
    return before - after


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _prune_snapshot_files(
    root: Path,
    *,
    now: datetime,
    policy: RuntimeRetentionPolicy,
    protected_roots: tuple[Path, ...] = (),
) -> dict[str, int]:
    if not root.exists():
        return {"kept": 0, "removed": 0, "protected": 0}
    files = sorted(
        (path for path in root.rglob("*") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    protected = [
        path
        for path in files
        if any(_is_within(path, protected_root) for protected_root in protected_roots)
    ]
    candidates = [path for path in files if path not in protected]
    cutoff = now - timedelta(days=policy.snapshot_days)
    keep_latest = set(candidates[: policy.keep_latest_files])
    removed = 0
    for path in candidates:
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if path in keep_latest or modified >= cutoff:
            continue
        path.unlink()
        removed += 1
    return {
        "kept": len(files) - removed,
        "removed": removed,
        "protected": len(protected),
    }


def compact_runtime_state(
    data_dir: Path,
    *,
    now: datetime | None = None,
    policy: RuntimeRetentionPolicy | None = None,
) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    policy = policy or RuntimeRetentionPolicy()

    predictions = _prune_predictions(data_dir, now=now, policy=policy)
    reconciliation = _prune_jsonl_tree(
        data_dir / "predictions" / "reconciliations",
        timestamp_fields=("resolved_at", "predicted_as_of"),
        cutoff=now - timedelta(days=policy.reconciliation_days),
    )
    ledgers = {
        name: _prune_jsonl_tree(
            data_dir / name,
            timestamp_fields=("as_of", "timestamp", "created_at"),
            cutoff=now - timedelta(days=policy.operational_ledger_days),
        )
        for name in ("signals", "orders", "trades")
    }

    reviews = _prune_jsonl_path(
        data_dir / "training" / "reviews.jsonl",
        timestamp_fields=("as_of", "created_at"),
        cutoff=now - timedelta(days=policy.training_audit_days),
    )

    app_db = _compact_app_db(
        data_dir / "persistence" / "app.sqlite",
        now=now,
        policy=policy,
    )
    market_removed = _compact_market_db(
        data_dir / "market" / "market_data.sqlite",
        now=now,
        policy=policy,
    )

    strategy_league_root = data_dir / "metrics" / "strategy_league"
    snapshots = {
        "reports": _prune_snapshot_files(
            data_dir / "reports",
            now=now,
            policy=policy,
        ),
        "snapshots": _prune_snapshot_files(
            data_dir / "snapshots",
            now=now,
            policy=policy,
        ),
        "metrics": _prune_snapshot_files(
            data_dir / "metrics",
            now=now,
            policy=policy,
            protected_roots=(strategy_league_root,),
        ),
    }

    result: dict[str, Any] = {
        "schema_version": 1,
        "compacted_at": now.isoformat(),
        "policy": asdict(policy),
        "predictions": predictions,
        "reconciliations": reconciliation,
        "operational_ledgers": ledgers,
        "training_reviews": reviews,
        "app_db_rows_removed": app_db,
        "market_rows_removed": market_removed,
        "snapshot_files": snapshots,
        "safety": {
            "old_unresolved_predictions_are_retained": True,
            "malformed_jsonl_fails_closed": True,
            "strategy_league_evidence_is_retained": True,
        },
    }
    metrics_path = data_dir / "metrics" / "runtime_retention_latest.json"
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Compact rolling runtime state without losing unresolved predictions"
        )
    )
    parser.add_argument("--data-dir", default="data")
    args = parser.parse_args()
    result = compact_runtime_state(Path(args.data_dir))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
