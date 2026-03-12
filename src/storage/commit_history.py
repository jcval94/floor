from __future__ import annotations

import subprocess


def commit_if_changed(message: str, paths: list[str] | None = None) -> dict:
    tracked_paths = paths or ["."]

    status = subprocess.run(
        ["git", "status", "--porcelain", "--", *tracked_paths],
        check=True,
        capture_output=True,
        text=True,
    )
    if not status.stdout.strip():
        return {"committed": False, "reason": "no_changes"}

    subprocess.run(["git", "add", "--", *tracked_paths], check=True)
    commit = subprocess.run(
        ["git", "commit", "-m", message],
        check=True,
        capture_output=True,
        text=True,
    )
    return {
        "committed": True,
        "reason": "changes_committed",
        "stdout": commit.stdout.strip(),
    }
