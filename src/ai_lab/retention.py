"""Dry-run retention only. Existing data deletion is deliberately not exposed."""

import time
from ai_lab.store import TERMINAL


def dry_run(store, now=None):
    now = time.time() if now is None else now
    protected = []
    candidates = []
    with store.connect() as c:
        runs = [dict(r) for r in c.execute("SELECT run_id,state,finished FROM runs")]
    for run in runs:
        path = store.root / "runs" / run["run_id"]
        age = (now - (run["finished"] or now)) / 86400
        for p in path.glob("*"):
            # Protect minimum evidence, exact OOS outputs and all active job files.
            if (
                p.is_symlink()
                or run["state"] not in TERMINAL
                or p.name
                in (
                    "provenance.json",
                    "result.json",
                    "artifacts.json",
                    "predictions.json",
                    "failure.json",
                    "completion.json",
                )
            ):
                protected.append(str(p.relative_to(store.root)))
            elif age > 30 and p.name == "report.html":
                candidates.append(
                    {
                        "path": str(p.relative_to(store.root)),
                        "bytes": p.stat().st_size,
                        "classification": "reconstructible_from_preserved_metrics_and_code",
                        "age_days": age,
                    }
                )
    return {
        "dry_run": True,
        "deletion_implemented": False,
        "requires_operator_review": True,
        "candidates": candidates,
        "protected_files": protected,
        "datasets": "protected while referenced by any accepted run",
        "official_releases": "never in scope",
        "mlflow": "protected; separate reviewed GC required",
        "projected_bytes": sum(p["bytes"] for p in candidates),
    }
