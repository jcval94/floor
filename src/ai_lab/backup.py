"""Research-volume backup/restore CLI. Never handles official runtime Releases."""

import argparse
import fcntl
import json
from pathlib import Path
import shutil
import sqlite3
from ai_lab.store import Store, digest, canonical


def backup(store, destination):
    if destination.exists():
        raise ValueError("BACKUP_DESTINATION_MUST_NOT_EXIST")
    destination = destination.resolve()
    if store.root == destination or store.root in destination.parents:
        raise ValueError("BACKUP_MUST_BE_OUTSIDE_RESEARCH_ROOT")
    with (store.root / "worker.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        destination.mkdir(parents=True, mode=0o700)
        # Operator also stops MCP/import writes for a complete volume checkpoint.
        for path in store.root.rglob("*"):
            relative = path.relative_to(store.root)
            if path.is_symlink():
                raise ValueError("SYMLINK_NOT_BACKED_UP")
            if (
                not path.is_file()
                or path.name.endswith(("-wal", "-shm"))
                or path.name == "worker.lock"
            ):
                continue
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if path.suffix == ".sqlite":
                with (
                    sqlite3.connect(
                        "file:" + str(path) + "?mode=ro", uri=True
                    ) as source,
                    sqlite3.connect(target) as dest,
                ):
                    source.backup(dest)
            else:
                shutil.copy2(path, target)
        files = {
            str(p.relative_to(destination)): digest(p.read_bytes())
            for p in destination.rglob("*")
            if p.is_file()
        }
        (destination / "backup_manifest.json").write_text(
            canonical(
                {"schema_version": 1, "source_root": str(store.root), "files": files}
            )
        )
    return {
        "files": len(files),
        "manifest_sha256": digest((destination / "backup_manifest.json").read_bytes()),
    }


def restore(backup_path, destination):
    if destination.exists():
        raise ValueError("RESTORE_DESTINATION_MUST_NOT_EXIST")
    manifest = json.loads((backup_path / "backup_manifest.json").read_text())
    for name, expected in manifest["files"].items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("INVALID_BACKUP_MEMBER")
        source = backup_path / relative
        if source.is_symlink() or digest(source.read_bytes()) != expected:
            raise ValueError("BACKUP_HASH_MISMATCH")
    shutil.copytree(
        backup_path, destination, ignore=shutil.ignore_patterns("backup_manifest.json")
    )
    # MLflow stores absolute local artifact URIs. Rebase only this volume's
    # prefix so subsequent restored runs cannot write back to the original root.
    tracking = destination / "mlflow.sqlite"
    if tracking.exists():
        old_prefix = Path(manifest["source_root"]).as_uri() + "/"
        new_prefix = destination.resolve().as_uri() + "/"
        with sqlite3.connect(tracking) as c:
            for table, key, field in (
                ("experiments", "experiment_id", "artifact_location"),
                ("runs", "run_uuid", "artifact_uri"),
            ):
                for row_id, uri in c.execute(f"SELECT {key},{field} FROM {table}"):
                    if isinstance(uri, str) and uri.startswith(old_prefix):
                        c.execute(
                            f"UPDATE {table} SET {field}=? WHERE {key}=?",
                            (new_prefix + uri[len(old_prefix) :], row_id),
                        )
    store = Store(destination)
    with store.connect() as c:
        if c.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("RESTORED_INTEGRITY_FAILED")
    return store


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("operation", choices=["backup", "restore"])
    p.add_argument("source", type=Path)
    p.add_argument("destination", type=Path)
    a = p.parse_args()
    if a.operation == "backup":
        print(canonical(backup(Store(a.source), a.destination)))
    else:
        restore(a.source, a.destination)
        print('{"restored":true,"scope":"research-only"}')
