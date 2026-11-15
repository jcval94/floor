"""Read-only operational evidence; no cloud calls, cleanup or secret output."""

import argparse
from collections import Counter
import json
import math
import os
from pathlib import Path
import re
import time

from ai_lab.store import Store, TERMINAL, canonical


def timing(values):
    values = sorted(values)
    return {
        "samples": len(values),
        "total_seconds": sum(values),
        "mean_seconds": sum(values) / len(values) if values else None,
        "p95_seconds": values[math.ceil(len(values) * 0.95) - 1] if values else None,
        "p95_method": "nearest_rank",
    }


def usage(store, actor, days=7, now=None):
    """Actor=None is operator/admin scope. Request callers must enforce identity."""
    if type(days) is not int or not 1 <= days <= 90:
        raise ValueError("INVALID_USAGE_WINDOW")
    now = time.time() if now is None else now
    where = "created>=? AND created<=?"
    args = [now - days * 86400, now]
    if actor is not None:
        where += " AND actor=?"
        args.append(actor)
    with store.connect() as c:
        runs = [
            dict(r)
            for r in c.execute(
                "SELECT run_id,state,created,started,finished,plan,error FROM runs WHERE "
                + where,
                args,
            )
        ]
        events = [
            dict(r)
            for r in c.execute(
                "SELECT o.state,o.kind,o.attempts,n.transport FROM outbox o "
                "JOIN runs r USING(run_id) LEFT JOIN notification_receipts n USING(event_id) "
                "WHERE " + where,
                args,
            )
        ]
    recipes = {}
    errors = Counter()
    queue, runtime = [], []
    for run in runs:
        recipe = json.loads(run["plan"])["recipe"]
        bucket = recipes.setdefault(recipe, {"runs": 0, "runtime_seconds": 0.0})
        bucket["runs"] += 1
        if run["started"] is not None:
            queue.append(max(0, run["started"] - run["created"]))
        if run["finished"] is not None and run["started"] is not None:
            elapsed = max(0, run["finished"] - run["started"])
            runtime.append(elapsed)
            bucket["runtime_seconds"] += elapsed
        if run["error"]:
            code = json.loads(run["error"]).get("code", "UNCLASSIFIED")
            # Never reflect arbitrary stored exception details to a client.
            errors[
                code
                if re.fullmatch(r"[A-Z][A-Z_0-9]{0,79}", str(code))
                else "UNCLASSIFIED"
            ] += 1
    return {
        "as_of": now,
        "scope": "all_actors" if actor is None else "authenticated_actor",
        "window_days": days,
        "window_basis": "jobs accepted in this interval; notification rows for those jobs",
        "accepted_runs": len(runs),
        "states": dict(Counter(r["state"] for r in runs)),
        "terminal_runs": sum(r["state"] in TERMINAL for r in runs),
        "errors_by_code": dict(errors),
        "queue_time": timing(queue),
        "execution_time": timing(runtime),
        "recipes": recipes,
        "notifications": {
            "states": dict(Counter(e["state"] for e in events)),
            "retry_attempts": sum(max(0, e["attempts"] - 1) for e in events),
            "api_acknowledgements": sum(
                e["transport"] == "slack_web_api_ack" for e in events
            ),
            "injected_receipts": sum(
                e["transport"] == "injected_unverified" for e in events
            ),
            "legacy_delivered_without_receipt": sum(
                e["state"] == "DELIVERED" and e["transport"] is None for e in events
            ),
            "independently_verified_live_deliveries": None,
        },
        "cost": {
            "estimated_incremental_cloud_usd": 0,
            "profile": "local_only_no_cloud_provisioning",
            "provider_invoice_verified": False,
            "measured_monetary_cost_usd": None,
            "excluded": [
                "electricity",
                "hardware",
                "internet",
                "AI_client_plan",
                "maintenance",
            ],
        },
        "limitations": [
            "Not a provider billing record or official FLOOR operational health check.",
            "A receipt records a response, not independent workspace confirmation; fixtures must never count as live evidence.",
            "Job acceptance counts exclude rejected requests; active-job runtime is not included in completed timing samples.",
        ],
    }


def readiness(store):
    """Offline preflight. Config presence never implies connection or deployment."""
    with store.connect() as c:
        integrity = c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        versions = [
            r[0]
            for r in c.execute("SELECT version FROM schema_version ORDER BY version")
        ]
    categories = Counter()
    symlinks = 0
    # Do not dereference links or enumerate anything outside the research volume.
    for root, directories, files in os.walk(store.root, followlinks=False):
        for name in list(directories):
            if (Path(root) / name).is_symlink():
                directories.remove(name)
                symlinks += 1
        for name in files:
            p = Path(root) / name
            if p.is_symlink():
                symlinks += 1
                continue
            relative = p.relative_to(store.root)
            category = relative.parts[0] if len(relative.parts) > 1 else "metadata"
            categories[category] += p.stat().st_size
    total = sum(categories.values())
    configured = bool(
        os.getenv("FLOOR_SLACK_BOT_TOKEN") and os.getenv("FLOOR_SLACK_CHANNEL_ID")
    )
    blockers = [
        "REMOTE_HOST_NOT_VERIFIED",
        "LIVE_SLACK_E2E_NOT_VERIFIED",
        "ASSISTANT_ACCOUNT_CONNECTIONS_NOT_VERIFIED",
    ]
    if not configured:
        blockers.append("SLACK_NOT_CONFIGURED")
    if not integrity:
        blockers.append("CONTROL_DB_INTEGRITY_FAILED")
    if total > 512 * 1024**2:
        blockers.append("BLOCKED_QUOTA")
    if symlinks:
        blockers.append("RESEARCH_VOLUME_SYMLINKS")
    return {
        "as_of": time.time(),
        "mode": "offline_read_only_preflight",
        "platform_complete": False,
        "control_db_integrity_ok": integrity,
        "schema_versions": versions,
        "slack_configuration_present": configured,
        "network_requests": 0,
        "messages_sent": 0,
        "storage": {
            "bytes": total,
            "limit_bytes": 512 * 1024**2,
            "by_category": dict(categories),
            "symlinks_skipped": symlinks,
        },
        "blockers": blockers,
        "next_steps": [
            "Operator installs an independent supervised worker on an authorized persistent host.",
            "Authorized Slack administrator installs App with chat:write and invites bot to selected private channel; inject secrets outside Git.",
            "Use floor_test_slack_connection for auth only, then reconcile at least ten distinct terminal experiment receipts against actual messages.",
            "Verify private HTTPS/auth and two actual assistant clients; retain version/config/evidence.",
        ],
        "warnings": [
            "No deletion, credential validation, worker liveness or deployment is inferred by this offline command."
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["readiness", "usage"])
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()
    # Prevent an accidental new empty volume being mistaken for the live lab.
    if not (args.root / "control.sqlite").is_file():
        raise SystemExit("RESEARCH_VOLUME_NOT_FOUND")
    store = Store(args.root)
    print(
        canonical(
            readiness(store)
            if args.operation == "readiness"
            else usage(store, None, args.days)
        )
    )


if __name__ == "__main__":
    main()
