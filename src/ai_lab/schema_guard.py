"""Detect schema drift on read-only research copies, without changing FLOOR DBs."""

import argparse
import hashlib
import json
from pathlib import Path

from ai_lab.catalog import inspect_db


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def schema(profile):
    """Separate schema evidence from changing row counts/file bytes/hash."""
    if "schema_objects" not in profile or any(
        "columns" not in index or "ddl" not in index
        for table in profile["tables"]
        for index in table["indexes"]
    ):
        raise ValueError("BASELINE_SCHEMA_METADATA_INCOMPLETE")
    tables = []
    for table in sorted(profile["tables"], key=lambda t: t["table"]):
        tables.append(
            {
                "table": table["table"],
                "ddl": table["ddl"],
                "columns": table["columns"],
                "foreign_keys": table["foreign_keys"],
                "indexes": [
                    {k: v for k, v in index.items() if k != "seq"}
                    for index in sorted(table["indexes"], key=lambda i: i["name"])
                ],
            }
        )
    return {"tables": tables, "schema_objects": profile["schema_objects"]}


def compare(expected, observed):
    old, new = schema(expected), schema(observed)
    old_tables = {t["table"]: t for t in old["tables"]}
    new_tables = {t["table"]: t for t in new["tables"]}
    changes = {
        "added_tables": sorted(new_tables.keys() - old_tables.keys()),
        "removed_tables": sorted(old_tables.keys() - new_tables.keys()),
        "changed_tables": sorted(
            k
            for k in old_tables.keys() & new_tables.keys()
            if old_tables[k] != new_tables[k]
        ),
        "views_or_triggers_changed": old["schema_objects"] != new["schema_objects"],
    }
    drift = old != new
    return {
        "database": observed["database"],
        "schema_matches": not drift,
        "integrity_ok": observed["integrity_check"] == "ok",
        "expected_schema_sha256": fingerprint(old),
        "observed_schema_sha256": fingerprint(new),
        "changes": changes,
        "row_counts": {t["table"]: t["row_count"] for t in observed["tables"]},
        "observed_database_sha256": observed["sha256"],
        "mutation_performed": False,
        "limitations": [
            "Schema equality does not prove freshness, logical join integrity, data vintage or multi-session operational correctness."
        ],
    }


def check(catalog, paths):
    if not catalog or not paths:
        raise ValueError("CATALOG_DATABASE_SET_MISMATCH")
    baselines = {p["database"]: p for p in catalog}
    if len(baselines) != len(catalog):
        raise ValueError("DUPLICATE_BASELINE_DATABASE")
    names = [p.name for p in paths]
    if len(set(names)) != len(names) or set(names) != set(baselines):
        raise ValueError("CATALOG_DATABASE_SET_MISMATCH")
    reports = [compare(baselines[p.name], inspect_db(p)) for p in paths]
    return {
        "ok": all(r["schema_matches"] and r["integrity_ok"] for r in reports),
        "scope": "read_only_copies",
        "databases": reports,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("databases", nargs="+", type=Path)
    args = parser.parse_args()
    # Never replace a database/catalog with the generated impact report.
    if args.output.resolve() in {p.resolve() for p in [args.catalog, *args.databases]}:
        raise SystemExit("OUTPUT_MUST_NOT_REPLACE_INPUT")
    report = check(json.loads(args.catalog.read_text()), args.databases)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"ok": report["ok"], "databases": len(report["databases"])}))
    raise SystemExit(0 if report["ok"] else 1)


if __name__ == "__main__":
    main()
