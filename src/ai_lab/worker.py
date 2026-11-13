"""Supervise outside the AI client: python -m ai_lab.worker --root /volume/lab."""

import argparse
import fcntl
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import sys
import time
from ai_lab.contracts import Plan
from ai_lab.store import Store
from ai_lab.slack import drain


def limits(plan):
    def apply():
        # Linux: kill child if its supervising worker disappears unexpectedly.
        import ctypes

        ctypes.CDLL(None).prctl(1, signal.SIGKILL)
        if os.getppid() == 1:
            os._exit(125)
        resource.setrlimit(
            resource.RLIMIT_CPU, (plan.max_seconds, plan.max_seconds + 1)
        )
        size = plan.max_memory_mb * 1024**2
        resource.setrlimit(resource.RLIMIT_AS, (size, size))
        resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024**2, 32 * 1024**2))
        resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
        os.umask(0o077)

    return apply


def tick(store):
    # Caller holds process-level flock: exactly one worker per volume.
    with store.transaction() as c:
        row = c.execute(
            "SELECT * FROM runs WHERE state='QUEUED' AND cancel=0 ORDER BY created LIMIT 1"
        ).fetchone()
        if not row:
            return False
        run = dict(row)
        c.execute(
            "UPDATE runs SET state='RUNNING',started=? WHERE run_id=?",
            (time.time(), run["run_id"]),
        )
        store.event(c, run["run_id"], "RUNNING", {"state": "RUNNING"})
    from ai_lab.provenance import code_identity

    accepted = store.get(run["run_id"])["acceptance_provenance"]
    current = code_identity()
    if not accepted or any(
        accepted[k] != current[k]
        for k in ("git_commit", "source_hashes", "dependencies", "python")
    ):
        store.finish(
            run["run_id"],
            "FAILED",
            error={
                "code": "CODE_OR_ENV_CHANGED_AFTER_ACCEPTANCE",
                "stage": "provenance_gate",
                "recoverable": False,
            },
        )
        return True
    plan = Plan.model_validate_json(run["plan"])
    out = store.root / "runs" / run["run_id"]
    out.mkdir(parents=True, exist_ok=True)
    env = {
        k: v
        for k, v in os.environ.items()
        if k in ("PATH", "PYTHONPATH", "LANG", "LC_ALL", "TZ")
    }
    env.update(
        {
            "HOME": str(out),
            "OPENBLAS_NUM_THREADS": "1",
            "OMP_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "MLFLOW_DISABLE_TELEMETRY": "true",
        }
    )
    start = time.monotonic()
    child = subprocess.Popen(
        [sys.executable, "-m", "ai_lab.execute", str(store.root), run["run_id"]],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        preexec_fn=limits(plan),
    )
    state = None
    next_notification = 0.0
    try:
        while child.poll() is None:
            if time.monotonic() >= next_notification:
                drain(store)
                next_notification = time.monotonic() + 1.1
            if store.get(run["run_id"])["cancel"]:
                state = "CANCELLED"
            elif time.monotonic() - start > plan.max_seconds:
                state = "TIMED_OUT"
            if state:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
                break
            time.sleep(0.1)
        if state:
            store.finish(
                run["run_id"],
                state,
                error={"code": state, "stage": "worker", "recoverable": False},
            )
        elif child.returncode == 0 and (out / "completion.json").exists():
            result = json.loads((out / "completion.json").read_text())
            result["evaluation_seconds"] = result["duration_seconds"]
            result["duration_seconds"] = time.monotonic() - start
            result["duration_scope"] = (
                "worker wall time including child imports, evaluation and tracking"
            )
            store.finish(run["run_id"], "SUCCEEDED", result=result)
        else:
            error = (
                json.loads((out / "failure.json").read_text())
                if (out / "failure.json").exists()
                else {
                    "code": "CHILD_FAILED",
                    "exit_code": child.returncode,
                    "stage": "worker",
                    "recoverable": False,
                }
            )
            store.finish(run["run_id"], "FAILED", error=error)
    finally:
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait()
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    store = Store(args.root)
    with (store.root / "worker.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # On restart, preserve uncertainty; accepted work is not silently rerun.
        with store.connect() as c:
            interrupted = [
                r[0] for r in c.execute("SELECT run_id FROM runs WHERE state='RUNNING'")
            ]
        for run_id in interrupted:
            store.finish(
                run_id,
                "FAILED",
                error={
                    "code": "WORKER_INTERRUPTED",
                    "stage": "recovery",
                    "recoverable": True,
                },
            )
        while True:
            ran = tick(store)
            drain(store)
            if args.once:
                break
            if not ran:
                time.sleep(1.1)


if __name__ == "__main__":
    main()
