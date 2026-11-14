"""Read-only schema profiler. SQL identifiers originate in sqlite_master only."""

import argparse
import json
from pathlib import Path
import sqlite3
import hashlib


def inspect_db(path):
    c = sqlite3.connect("file:" + str(path.resolve()) + "?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    tables = []
    for row in c.execute(
        "SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ):
        name = row["name"]
        quoted = '"' + name.replace('"', '""') + '"'
        tables.append(
            {
                "table": name,
                "ddl": row["sql"],
                "columns": [
                    dict(x) for x in c.execute("PRAGMA table_info(" + quoted + ")")
                ],
                "foreign_keys": [
                    dict(x)
                    for x in c.execute("PRAGMA foreign_key_list(" + quoted + ")")
                ],
                "indexes": [
                    dict(x) for x in c.execute("PRAGMA index_list(" + quoted + ")")
                ],
                "row_count": c.execute("SELECT count(*) FROM " + quoted).fetchone()[0],
            }
        )
    return {
        "database": path.name,
        "bytes": path.stat().st_size,
        "sha256": hashlib.file_digest(path.open("rb"), "sha256").hexdigest(),
        "integrity_check": c.execute("PRAGMA integrity_check").fetchone()[0],
        "tables": tables,
    }


def render(catalog):
    text = [
        "# Observed snapshot schema catalog",
        "",
        "Generated read-only from the verified immutable run-43303225713-a1 snapshot; not current production. No row values are exported. SQL defaults, FK and index declarations are observed, not inferred. Logical relationships require business-key review before migration.",
        "",
    ]
    for db in catalog:
        text.extend(
            [
                "## " + db["database"],
                "",
                f"Bytes: {db['bytes']}; SHA-256: `{db['sha256']}`; integrity: `{db['integrity_check']}`.",
                "",
            ]
        )
        for t in db["tables"]:
            text.extend(
                [
                    "### " + t["table"],
                    "",
                    f"Observed rows: {t['row_count']}; declared foreign keys: {len(t['foreign_keys'])}.",
                    "",
                    "| Field | Type | NOT NULL | PK ordinal | Default |",
                    "|---|---|---|---|---|",
                ]
            )
            for col in t["columns"]:
                text.append(
                    "| "
                    + " | ".join(
                        str(col[k]).replace("|", "\\|")
                        for k in ["name", "type", "notnull", "pk", "dflt_value"]
                    )
                    + " |"
                )
            text.extend(
                [
                    "",
                    "```sql",
                    t["ddl"],
                    "```",
                    "",
                    "Indexes: `" + json.dumps(t["indexes"]) + "`",
                    "",
                ]
            )
    return "\n".join(text)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("databases", nargs="+", type=Path)
    p.add_argument("--output", required=True, type=Path)
    a = p.parse_args()
    catalog = [inspect_db(path) for path in a.databases]
    a.output.write_text(render(catalog))
    a.output.with_suffix(".json").write_text(json.dumps(catalog, indent=2))
