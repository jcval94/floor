from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


TIME_CANDIDATES = ["as_of", "timestamp", "ts_utc", "resolved_at", "created_at", "updated_at"]
ORDER_CANDIDATES = ["id", "as_of", "timestamp", "ts_utc"]


@dataclass
class TableAudit:
    table: str
    row_count: int
    latest_col: str | None
    latest_value: str | None
    age_days: int | None
    freshness_status: str
    head_path: str


def _parse_dt(value: object) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info('{table}')").fetchall()
    return [str(r[1]) for r in rows]


def _row_to_dict(columns: list[str], row: tuple) -> dict:
    return {col: row[idx] for idx, col in enumerate(columns)}


def _write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _latest_timestamp(conn: sqlite3.Connection, table: str, columns: list[str]) -> tuple[str | None, str | None, int | None, str]:
    now = datetime.now(tz=timezone.utc)
    for col in TIME_CANDIDATES:
        if col not in columns:
            continue
        value = conn.execute(f"SELECT MAX({col}) FROM {table}").fetchone()[0]
        parsed = _parse_dt(value)
        if parsed is None:
            return col, None if value is None else str(value), None, "unknown"
        age_days = (now - parsed).days
        status = "fresh" if age_days <= 7 else "stale"
        return col, parsed.isoformat(), age_days, status
    return None, None, None, "missing_timestamp"


def _head_rows(conn: sqlite3.Connection, table: str, columns: list[str], limit: int) -> list[dict]:
    order_col = next((c for c in ORDER_CANDIDATES if c in columns), None)
    order_clause = f"ORDER BY {order_col} DESC" if order_col else ""
    rows = conn.execute(f"SELECT * FROM {table} {order_clause} LIMIT {limit}").fetchall()
    return [_row_to_dict(columns, row) for row in rows]


def run_audit(db_path: Path, output_dir: Path, limit: int) -> list[TableAudit]:
    print(f"[db-audit] STEP 1/5 START open sqlite db={db_path}")
    if not db_path.exists():
        raise FileNotFoundError(f"SQLite DB not found: {db_path}")

    output_dir.mkdir(parents=True, exist_ok=True)
    audits: list[TableAudit] = []

    with sqlite3.connect(db_path) as conn:
        print("[db-audit] STEP 1/5 DONE sqlite connection established")
        print("[db-audit] STEP 2/5 START discover tables")
        tables = [
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        ]
        print(f"[db-audit] STEP 2/5 DONE tables_found={len(tables)}")

        print("[db-audit] STEP 3/5 START export heads and run freshness checks")
        for table in tables:
            columns = _table_columns(conn, table)
            if not columns:
                continue
            row_count = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            latest_col, latest_value, age_days, freshness_status = _latest_timestamp(conn, table, columns)
            head_rows = _head_rows(conn, table, columns, limit=limit)

            head_path = output_dir / f"{table}_head{limit}.csv"
            _write_csv(head_path, head_rows, columns)

            print(
                "[db-audit] table=%s rows=%s latest_col=%s latest_value=%s age_days=%s status=%s head_rows=%s DONE"
                % (table, row_count, latest_col, latest_value, age_days, freshness_status, len(head_rows))
            )

            audits.append(
                TableAudit(
                    table=table,
                    row_count=row_count,
                    latest_col=latest_col,
                    latest_value=latest_value,
                    age_days=age_days,
                    freshness_status=freshness_status,
                    head_path=str(head_path),
                )
            )
        print("[db-audit] STEP 3/5 DONE per-table audit complete")

    print("[db-audit] STEP 4/5 START write summary files")
    summary_json = output_dir / "summary.json"
    summary_md = output_dir / "summary.md"
    payload = {
        "generated_at_utc": datetime.now(tz=timezone.utc).isoformat(),
        "db_path": str(db_path),
        "tables": [a.__dict__ for a in audits],
    }
    summary_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = ["# DB Table Audit", "", f"DB: `{db_path}`", "", "| table | rows | latest_col | latest_value | age_days | status |", "|---|---:|---|---|---:|---|"]
    for audit in audits:
        lines.append(
            f"| {audit.table} | {audit.row_count} | {audit.latest_col or '-'} | {audit.latest_value or '-'} | {audit.age_days if audit.age_days is not None else '-'} | {audit.freshness_status} |"
        )
    summary_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[db-audit] STEP 4/5 DONE wrote {summary_json.name} and {summary_md.name}")

    stale_tables = [a.table for a in audits if a.freshness_status == "stale"]
    empty_tables = [a.table for a in audits if a.row_count == 0]
    print(
        "[db-audit] STEP 5/5 DONE audit finished tables=%s stale=%s empty=%s"
        % (len(audits), len(stale_tables), len(empty_tables))
    )

    return audits


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit all sqlite tables and export head rows as CSV files")
    parser.add_argument("--db", default="data/persistence/app.sqlite", help="Path to sqlite db")
    parser.add_argument("--out-dir", default="experimento/artifacts/db_audit", help="Output directory")
    parser.add_argument("--head", type=int, default=5, help="Rows to export per table")
    args = parser.parse_args()

    run_audit(Path(args.db), Path(args.out_dir), args.head)


if __name__ == "__main__":
    main()
