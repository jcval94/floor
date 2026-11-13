"""Capture reproducibility before accepting work, including failed executions."""

from importlib.metadata import version, PackageNotFoundError
from pathlib import Path
import platform
import subprocess
from ai_lab.store import digest


def code_identity():
    root = Path(__file__).resolve().parents[2]
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        raise ValueError("SOURCE_COMMIT_UNAVAILABLE") from None
    hashes = {
        str(p.relative_to(root)): digest(p.read_bytes())
        for p in sorted((root / "src").rglob("*.py"))
    }
    packages = {}
    for name in ("numpy", "scikit-learn", "mcp", "mlflow-skinny", "exchange-calendars"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = "NOT_INSTALLED"
    return {
        "git_commit": commit,
        "source_hashes": hashes,
        "dependencies": packages,
        "python": platform.python_version(),
    }


def acceptance(plan, actor, manifest):
    return {
        **code_identity(),
        "plan": plan.model_dump(),
        "requester": actor,
        "dataset_manifest": manifest,
        "phase": "accepted_before_execution",
        "parent_run_id": None,
    }
