"""Official MCP SDK transports, sharing the same service as the worker and CLI."""

import argparse
from contextvars import ContextVar
import hashlib
import hmac
import json
import os
from pathlib import Path
import time
from typing import Literal
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from ai_lab.contracts import Plan, Result, RECIPES, MODELS, CONTRACT_VERSION
from ai_lab.store import Store, TERMINAL

principal = ContextVar("principal", default=None)
ROLES = {
    "read_only": 0,
    "experiment_runner": 1,
    "research_developer": 2,
    "administrator": 3,
}


def identity(required=0):
    current = principal.get()
    if current is None:
        raise ValueError("AUTHENTICATION_REQUIRED")
    if ROLES.get(current["role"], -1) < required:
        raise ValueError("FORBIDDEN_ROLE")
    return current


def guarded(fn, required=0):
    try:
        identity(required)
        return Result(data=fn())
    except (ValueError, KeyError) as exc:
        code = str(exc).strip("'")
        # Known validation codes only, no arbitrary row/secret echo.
        if not code.replace("_", "").isalnum() or not code.isupper():
            code = "INVALID_REQUEST"
        return Result(ok=False, data_status="BLOCKED", error={"code": code})
    except Exception:
        return Result(ok=False, data_status="BLOCKED", error={"code": "INTERNAL_ERROR"})


def build_server(store):
    mcp = FastMCP(
        "FLOOR AI Lab",
        host="127.0.0.1",
        port=int(os.environ.get("FLOOR_MCP_PORT", "8765")),
        stateless_http=True,
        json_response=True,
        max_request_body_size=65536,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1:*", "localhost:*"],
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*"],
        ),
    )

    def owned(run_id):
        user = identity()
        run = store.get(run_id)
        if user["role"] != "administrator" and run["actor"] != user["actor"]:
            raise ValueError("FORBIDDEN_OWNER")
        return run

    @mcp.tool()
    def floor_get_status() -> Result:
        """Read lab state only. Official league/EOD freshness is not inferred."""

        def read():
            user = identity()
            with store.connect() as c:
                counts = {
                    r[0]: r[1]
                    for r in c.execute(
                        "SELECT state,count(*) FROM runs WHERE actor=? GROUP BY state",
                        (user["actor"],),
                    )
                }
            return {
                "states": counts,
                "environment": "local-isolated",
                "official_runtime": "NOT_AVAILABLE",
                "remote_deployed": False,
                "cloud_budget_usd": 0,
                "slack_configured": bool(
                    os.getenv("FLOOR_SLACK_BOT_TOKEN")
                    and os.getenv("FLOOR_SLACK_CHANNEL_ID")
                ),
            }

        return guarded(read)

    @mcp.tool()
    def floor_get_capabilities() -> Result:
        """Discover implemented recipes and explicit unavailable capabilities."""
        return guarded(
            lambda: {
                "recipes": list(RECIPES),
                "models": list(MODELS),
                "contracts": CONTRACT_VERSION,
                "unavailable": [
                    "official_forecasts",
                    "opportunity_finder",
                    "strategy_backtest",
                    "arbitrary_code",
                    "automatic_promotion",
                    "public_remote_deployment",
                ],
                "limits": {
                    "jobs_per_day": 10,
                    "jobs_per_actor_day": 5,
                    "jobs_per_month": 100,
                    "concurrency": 1,
                    "max_seconds": 120,
                    "storage_mb": 512,
                },
            }
        )

    @mcp.tool()
    def floor_get_data_catalog(limit: int = 20, offset: int = 0) -> Result:
        """Page registered dataset manifests; content-addressed research inputs only."""

        def read():
            if not 1 <= limit <= 50 or not 0 <= offset <= 10000:
                raise ValueError("INVALID_PAGINATION")
            with store.connect() as c:
                records = c.execute(
                    "SELECT id,manifest FROM datasets ORDER BY id LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
            return {
                "datasets": [{"dataset_id": r[0], **json.loads(r[1])} for r in records],
                "next_offset": offset + limit if len(records) == limit else None,
            }

        return guarded(read)

    @mcp.tool()
    def floor_get_data_quality(plan: Plan) -> Result:
        """Verify checksums, temporal availability, uniqueness and target completeness."""
        return guarded(lambda: store.validate(plan))

    @mcp.tool()
    def floor_list_models() -> Result:
        """Discover research estimators and separately versioned frozen FLOOR W1 artifacts."""
        return guarded(
            lambda: {
                "models": [
                    {
                        "id": m,
                        "status": "EXPERIMENTAL",
                        "params": {
                            "dummy": "train mean",
                            "ridge": "alpha=1",
                            "extra_trees": "32 trees, depth=5, min_leaf=10",
                        }[m],
                    }
                    for m in MODELS
                ],
                "frozen_models": __import__(
                    "ai_lab.frozen_models", fromlist=["catalog"]
                ).catalog(),
                "warning": "Stored metrics are historical; use frozen_model_comparison for shared-window measured results. Training cutoff remains unverified.",
            }
        )

    @mcp.tool()
    def floor_list_features(limit: int = 20, offset: int = 0) -> Result:
        """Read FLOOR's existing registry. leakage_safe is a claim, not PIT proof."""

        def read():
            from dataclasses import asdict
            from features.feature_registry import get_feature_registry

            if not 1 <= limit <= 50 or not 0 <= offset <= 10000:
                raise ValueError("INVALID_PAGINATION")
            records = get_feature_registry()
            return {
                "features": [asdict(x) for x in records[offset : offset + limit]],
                "next_offset": offset + limit
                if offset + limit < len(records)
                else None,
                "warning": "Availability evidence required separately for each materialized feature.",
            }

        return guarded(read)

    @mcp.tool()
    def floor_plan_experiment(
        question: str,
        dataset_id: str,
        recipe: Literal[
            "model_comparison",
            "feature_audit",
            "feature_ablation",
            "frozen_model_comparison",
        ] = "model_comparison",
        drop_features: list[str] | None = None,
        frozen_model_ids: list[str] | None = None,
    ) -> Result:
        """Resolve a natural-language intent to an allowlisted plan. Unknown code/data requires review."""

        def plan_request():
            _, manifest = store.dataset(dataset_id)
            if len(question) > 2000 or len(question) < 5:
                raise ValueError("INVALID_QUESTION")
            supported_words = (
                "compara",
                "comparar",
                "compare",
                "ridge",
                "features",
                "feature",
                "audita",
                "audit",
                "elimina",
                "ablation",
            )
            if any(
                w in question.lower()
                for w in (
                    "vix",
                    "xgboost",
                    "lightgbm",
                    "backtest",
                    "estrategia",
                )
            ) or not any(w in question.lower() for w in supported_words):
                return {
                    "state": "AWAITING_APPROVAL",
                    "executable": False,
                    "question": question,
                    "required": [
                        "reviewed recipe PR",
                        "available_at evidence",
                        "dependency/security review",
                        "sandbox authorization",
                    ],
                    "reason": "Novel hypothesis is a proposal, never converted to arbitrary executable code.",
                }
            selected_recipe = (
                "frozen_model_comparison"
                if any(
                    w in question.lower()
                    for w in ("champion", "campeón", "campeon", "congelad")
                )
                else recipe
            )
            optional = (
                {"frozen_model_ids": frozen_model_ids}
                if frozen_model_ids is not None
                else {}
            )
            plan = Plan(
                question=question,
                dataset_id=dataset_id,
                recipe=selected_recipe,
                **optional,
                features=manifest["features"],
                drop_features=drop_features or [],
            )
            validation = store.validate(plan)
            return {
                "state": "PLANNED",
                "plan": plan.model_dump(),
                "validation": validation,
                "executable": True,
                "planner": "deterministic intent router; caller must review exact recipe and models, not a general NLP compiler",
            }

        return guarded(plan_request)

    @mcp.tool()
    def floor_validate_experiment(plan: Plan) -> Result:
        """Validate registered data and fixed resource policy without launching compute."""
        return guarded(lambda: store.validate(plan))

    @mcp.tool()
    def floor_estimate_experiment_cost(plan: Plan) -> Result:
        """Local cloud budget is exactly zero; electricity and maintenance excluded."""
        return guarded(
            lambda: {
                "cloud_usd": 0,
                "seconds_upper_bound": plan.max_seconds,
                "memory_mb_upper_bound": plan.max_memory_mb,
                "paid_backend_allowed": False,
                "runtime_estimate": "upper bound only, measured runtime retained after execution",
            }
        )

    @mcp.tool()
    def floor_submit_experiment(plan: Plan, idempotency_key: str) -> Result:
        """Durably queue an approved registered recipe. Heavy evaluation occurs in a separate worker."""

        def submit():
            run = store.submit(plan, identity(1)["actor"], idempotency_key)
            return {
                "experiment_id": run["experiment_id"],
                "run_id": run["run_id"],
                "state": run["state"],
            }

        return guarded(submit, 1)

    @mcp.tool()
    def floor_list_experiments(limit: int = 20, offset: int = 0) -> Result:
        """Page the authenticated actor's jobs; no global private run enumeration."""

        def read():
            if not 1 <= limit <= 50 or not 0 <= offset <= 10000:
                raise ValueError("INVALID_PAGINATION")
            with store.connect() as c:
                rows = c.execute(
                    "SELECT run_id,experiment_id,state,created,started,finished,error FROM runs WHERE actor=? ORDER BY created DESC LIMIT ? OFFSET ?",
                    (identity()["actor"], limit, offset),
                ).fetchall()
            return {
                "runs": [dict(r) for r in rows],
                "next_offset": offset + limit if len(rows) == limit else None,
            }

        return guarded(read)

    @mcp.tool()
    def floor_get_experiment_status(run_id: str) -> Result:
        """Read authoritative research job state and provenance/configuration."""
        return guarded(
            lambda: {
                k: v
                for k, v in owned(run_id).items()
                if k
                in (
                    "run_id",
                    "experiment_id",
                    "state",
                    "created",
                    "started",
                    "finished",
                    "cancel",
                    "error",
                )
            }
        )

    @mcp.tool()
    def floor_get_experiment_results(run_id: str) -> Result:
        """Fetch measured metrics or structured error; never infer success from notification state."""
        return guarded(
            lambda: {
                k: v
                for k, v in owned(run_id).items()
                if k in ("run_id", "experiment_id", "state", "result", "error")
            }
        )

    @mcp.tool()
    def floor_cancel_experiment(run_id: str) -> Result:
        """Request process termination; RUNNING stays active until the worker confirms cancellation."""
        return guarded(lambda: store.cancel(run_id, identity(1)["actor"]), 1)

    @mcp.tool()
    def floor_list_experiment_artifacts(run_id: str) -> Result:
        """List files/checksums relative to an operator-protected artifact volume; no public links."""

        def read():
            owned(run_id)
            path = store.root / "runs" / run_id / "artifacts.json"
            return {
                "artifacts": json.loads(path.read_text()) if path.exists() else {},
                "publicly_hosted": False,
            }

        return guarded(read)

    @mcp.tool()
    def floor_get_notification_status(run_id: str) -> Result:
        """A Slack message is delivered only with a real API acknowledgement and ts."""

        def read():
            owned(run_id)
            with store.connect() as c:
                rows = c.execute(
                    "SELECT event_id,kind,state,attempts,slack_ts,error FROM outbox WHERE run_id=? ORDER BY rowid",
                    (run_id,),
                ).fetchall()
            return {
                "configured": bool(
                    os.getenv("FLOOR_SLACK_BOT_TOKEN")
                    and os.getenv("FLOOR_SLACK_CHANNEL_ID")
                ),
                "events": [dict(r) for r in rows],
            }

        return guarded(read)

    @mcp.tool()
    def floor_resend_experiment_report(run_id: str) -> Result:
        """Requeue only known failed terminal reports; ambiguous deliveries require operator reconciliation."""

        def resend():
            run = owned(run_id)
            if run["state"] not in TERMINAL:
                raise ValueError("RUN_NOT_TERMINAL")
            with store.transaction() as c:
                n = c.execute(
                    "UPDATE outbox SET state='PENDING',attempts=0,next_at=0 WHERE run_id=? AND kind=? AND state='FAILED'",
                    (run_id, run["state"]),
                ).rowcount
            return {"requeued": n, "run_reexecuted": False}

        return guarded(resend, 1)

    @mcp.tool()
    def floor_test_slack_connection() -> Result:
        """Check configured Slack token via auth.test; does not prove channel delivery."""

        def test():
            from ai_lab.slack import test_connection

            return test_connection()

        return guarded(test, 1)

    @mcp.tool()
    def floor_compare_experiments(run_ids: list[str]) -> Result:
        """Compare only completed runs with identical datasets, features, folds and targets."""

        def compare():
            if not 2 <= len(run_ids) <= 5:
                raise ValueError("COMPARISON_SIZE_OUT_OF_POLICY")
            runs = [owned(run_id) for run_id in run_ids]
            if any(r["state"] != "SUCCEEDED" for r in runs):
                raise ValueError("COMPARISON_REQUIRES_SUCCESS")
            keys = (
                "dataset_id",
                "features",
                "folds",
                "seed",
                "objective",
                "drop_features",
                "recipe",
            )
            if runs[0]["plan"]["recipe"] == "frozen_model_comparison":
                keys = (*keys, "frozen_model_ids")
            first = {k: runs[0]["plan"].get(k) for k in keys}
            differences = [
                r["run_id"]
                for r in runs
                if {k: r["plan"].get(k) for k in keys} != first
            ]
            if differences:
                raise ValueError("NOT_COMPARABLE")
            evidence = [r.get("acceptance_provenance") or {} for r in runs]
            if any(
                e.get("source_hashes") != evidence[0].get("source_hashes")
                or e.get("asset_hashes") != evidence[0].get("asset_hashes")
                for e in evidence[1:]
            ):
                raise ValueError("SOURCE_OR_MODEL_ASSETS_NOT_COMPARABLE")
            if any(
                r["result"].get("comparability")
                != runs[0]["result"].get("comparability")
                for r in runs[1:]
            ):
                raise ValueError("EVALUATION_WINDOWS_NOT_COMPARABLE")
            return {
                "comparable": True,
                "conditions": first,
                "runs": [{"run_id": r["run_id"], "result": r["result"]} for r in runs],
                "warning": "Repeated evaluation of the same observations is not independent statistical evidence.",
            }

        return guarded(compare)

    @mcp.tool()
    def floor_get_run_provenance(run_id: str) -> Result:
        """Read recorded dataset, exact code fingerprints and environment versions."""

        def read():
            owned(run_id)
            path = store.root / "runs" / run_id / "provenance.json"
            if not path.exists():
                accepted = owned(run_id).get("acceptance_provenance")
                if not accepted:
                    raise ValueError("PROVENANCE_NOT_AVAILABLE")
                return accepted
            return json.loads(path.read_text())

        return guarded(read)

    @mcp.tool()
    def floor_get_retention_plan() -> Result:
        """Administrator dry-run only; never deletes datasets, snapshots or artifacts."""

        def read():
            from ai_lab.retention import dry_run

            return dry_run(store)

        return guarded(read, 3)

    return mcp


class PrivateHTTPAuth:
    """Loopback/tunnel profile, NOT a public OAuth authorization server."""

    def __init__(self, app, credentials):
        self.app, self.credentials, self.windows = app, credentials, {}

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        raw = headers.get(b"authorization", b"").decode(errors="ignore")
        hashed = (
            hashlib.sha256(raw[7:].encode()).hexdigest()
            if raw.startswith("Bearer ")
            else ""
        )
        user = next(
            (v for k, v in self.credentials.items() if hmac.compare_digest(k, hashed)),
            None,
        )
        status = 401
        if user:
            now = time.monotonic()
            key = user["actor"]
            timestamps = [t for t in self.windows.get(key, []) if now - t < 60]
            self.windows[key] = timestamps
            status = 429 if len(timestamps) >= 30 else 0
            if status == 0:
                timestamps.append(now)
                token = principal.set(user)
                try:
                    return await self.app(scope, receive, send)
                finally:
                    principal.reset(token)
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b'{"error":"unauthorized_or_rate_limited"}',
            }
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument(
        "--transport", choices=["stdio", "streamable-http"], default="stdio"
    )
    args = parser.parse_args()
    server = build_server(Store(args.root))
    if args.transport == "stdio":
        role = os.environ.get("FLOOR_LAB_ROLE", "read_only")
        if role not in ROLES:
            raise SystemExit("invalid configured role")
        principal.set(
            {"actor": os.environ.get("FLOOR_LAB_ACTOR", "local"), "role": role}
        )
        server.run(transport="stdio")
    else:
        import uvicorn

        credentials = json.loads(os.environ.get("FLOOR_LAB_TOKEN_HASHES", "{}"))
        if (
            not credentials
            or len(credentials) > 16
            or any(
                len(k) != 64 or v.get("role") not in ROLES or not v.get("actor")
                for k, v in credentials.items()
            )
        ):
            raise SystemExit(
                "private HTTP requires configured token hashes and identities"
            )
        app = server.streamable_http_app()
        uvicorn.run(
            PrivateHTTPAuth(app, credentials),
            host="127.0.0.1",
            port=server.settings.port,
            log_level="warning",
        )


if __name__ == "__main__":
    main()
