"""Transactional queue and outbox, on an operator-provisioned persistent volume."""

import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from ai_lab.contracts import Plan

TERMINAL = ("SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(data):
    return hashlib.sha256(data).hexdigest()


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = self.root / "control.sqlite"
        with self.connect() as c:
            c.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY);
            INSERT OR IGNORE INTO schema_version VALUES(1);
            CREATE TABLE IF NOT EXISTS datasets(id TEXT PRIMARY KEY, manifest TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS runs(
             run_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL, actor TEXT NOT NULL,
             idem TEXT NOT NULL, plan TEXT NOT NULL, state TEXT NOT NULL,
             created REAL NOT NULL, started REAL, finished REAL, cancel INTEGER DEFAULT 0,
             result TEXT, error TEXT, UNIQUE(actor,idem));
            CREATE TABLE IF NOT EXISTS outbox(
             event_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
             kind TEXT NOT NULL, payload TEXT NOT NULL, state TEXT DEFAULT 'PENDING',
             attempts INTEGER DEFAULT 0, next_at REAL DEFAULT 0,
             slack_ts TEXT, error TEXT, UNIQUE(run_id,kind));
            CREATE TABLE IF NOT EXISTS run_provenance(
             run_id TEXT PRIMARY KEY REFERENCES runs(run_id), payload TEXT NOT NULL);
            INSERT OR IGNORE INTO schema_version VALUES(2);
            CREATE TABLE IF NOT EXISTS audit(
             id INTEGER PRIMARY KEY, at REAL NOT NULL, actor TEXT NOT NULL,
             action TEXT NOT NULL, subject TEXT NOT NULL);
            """)

    def connect(self):
        c = sqlite3.connect(self.db, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        return c

    @contextmanager
    def transaction(self):
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            yield c

    def register(self, rows: list[dict], manifest: dict):
        # Operator-only import. MCP cannot select paths, URLs or write datasets.
        blob = canonical(rows).encode()
        manifest = {**manifest, "rows_hash": digest(blob), "rows": len(rows)}
        dataset_id = digest(canonical(manifest).encode())
        path = self.root / "datasets" / dataset_id
        path.mkdir(parents=True, exist_ok=True)
        (path / "rows.json").write_bytes(blob)
        (path / "manifest.json").write_text(canonical(manifest))
        with self.transaction() as c:
            c.execute(
                "INSERT OR IGNORE INTO datasets VALUES(?,?)",
                (dataset_id, canonical(manifest)),
            )
        return dataset_id

    def dataset(self, dataset_id):
        with self.connect() as c:
            record = c.execute(
                "SELECT manifest FROM datasets WHERE id=?", (dataset_id,)
            ).fetchone()
        if not record:
            raise ValueError("DATASET_NOT_REGISTERED")
        manifest = json.loads(record[0])
        if digest(canonical(manifest).encode()) != dataset_id:
            raise ValueError("MANIFEST_HASH_MISMATCH")
        blob = (self.root / "datasets" / dataset_id / "rows.json").read_bytes()
        if digest(blob) != manifest["rows_hash"]:
            raise ValueError("DATASET_HASH_MISMATCH")
        return json.loads(blob), manifest

    def validate(self, plan: Plan):
        from ai_lab.recipes import validate_rows

        if plan.recipe != "strategy_comparison" and plan.objective != "mae_floor_pct":
            raise ValueError("OBJECTIVE_NOT_VALID_FOR_RECIPE")
        rows, manifest = self.dataset(plan.dataset_id)
        validate_rows(rows, manifest, plan)
        recipe_details = {}
        if plan.recipe == "strategy_comparison":
            from ai_lab.strategy_replay import validate_strategy_inputs

            cfg, _, assets, cutoff, dates, symbols = validate_strategy_inputs(
                rows, manifest, plan
            )
            from contracts.trading import round_trip_cost_bps_from_contract

            recipe_details = {
                "evidence_type": "post_artifact_retrospective_replay",
                "out_of_sample_verified": False,
                "round_trip_cost_bps": round_trip_cost_bps_from_contract(cfg["costs"]),
                "strategies": plan.strategies,
                "model_assets": assets,
                "common_cutoff": cutoff.isoformat(),
                "start": dates[0],
                "end": dates[-1],
                "universe_including_benchmark": symbols,
            }
        if plan.recipe == "frozen_model_comparison":
            from ai_lab.frozen_models import validate_frozen_inputs

            validate_frozen_inputs(rows, manifest, plan)
        if len(set(plan.models)) != len(plan.models) or len(set(plan.features)) != len(
            plan.features
        ):
            raise ValueError("DUPLICATE_CANDIDATES")
        if plan.recipe == "feature_ablation" and (
            not plan.drop_features or not set(plan.drop_features) < set(plan.features)
        ):
            raise ValueError("ABLATION_REQUIRES_PROPER_SUBSET")
        return {
            "valid": True,
            "recipe_details": recipe_details,
            "rows": len(rows),
            "dataset": manifest,
            "cost_usd": 0,
            "cost_basis": "local compute; excludes electricity",
            "max_seconds": plan.max_seconds,
            "max_memory_mb": plan.max_memory_mb,
        }

    @staticmethod
    def event(c, run_id, kind, payload):
        c.execute(
            "INSERT OR IGNORE INTO outbox(event_id,run_id,kind,payload) VALUES(?,?,?,?)",
            (str(uuid.uuid4()), run_id, kind, canonical(payload)),
        )

    def submit(self, plan: Plan, actor: str, idem: str):
        if not 1 <= len(idem) <= 128:
            raise ValueError("INVALID_IDEMPOTENCY_KEY")
        encoded = canonical(plan.model_dump())
        # Check duplicate before quotas; an idempotent replay consumes no quota.
        with self.connect() as c:
            old = c.execute(
                "SELECT * FROM runs WHERE actor=? AND idem=?", (actor, idem)
            ).fetchone()
        if old:
            if Plan.model_validate_json(old["plan"]).model_dump() != plan.model_dump():
                raise ValueError("IDEMPOTENCY_CONFLICT")
            return dict(old)
        validation = self.validate(plan)
        from ai_lab.provenance import acceptance

        provenance = acceptance(plan, actor, validation["dataset"])
        with self.transaction() as c:
            old = c.execute(
                "SELECT * FROM runs WHERE actor=? AND idem=?", (actor, idem)
            ).fetchone()
            if old:
                if (
                    Plan.model_validate_json(old["plan"]).model_dump()
                    != plan.model_dump()
                ):
                    raise ValueError("IDEMPOTENCY_CONFLICT")
                return dict(old)
            now = time.time()
            day = int(now // 86400) * 86400
            month = time.strftime("%Y-%m-01", time.gmtime(now))
            month_start = __import__("calendar").timegm(
                time.strptime(month, "%Y-%m-%d")
            )
            counts = [
                c.execute(
                    "SELECT count(*) FROM runs WHERE created>=?", (t,)
                ).fetchone()[0]
                for t in (day, month_start)
            ]
            actor_count = c.execute(
                "SELECT count(*) FROM runs WHERE actor=? AND created>=?", (actor, day)
            ).fetchone()[0]
            active = c.execute(
                "SELECT count(*) FROM runs WHERE state IN ('QUEUED','RUNNING')"
            ).fetchone()[0]
            size = sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file())
            if (
                counts[0] >= 10
                or counts[1] >= 100
                or actor_count >= 5
                or active >= 10
                or size > 512 * 1024**2
            ):
                raise ValueError("BLOCKED_QUOTA")
            run_id, experiment_id = str(uuid.uuid4()), str(uuid.uuid4())
            c.execute(
                "INSERT INTO runs(run_id,experiment_id,actor,idem,plan,state,created) VALUES(?,?,?,?,?,?,?)",
                (run_id, experiment_id, actor, idem, encoded, "QUEUED", now),
            )
            provenance["run_id"] = run_id
            provenance["experiment_id"] = experiment_id
            c.execute(
                "INSERT INTO run_provenance(run_id,payload) VALUES(?,?)",
                (run_id, canonical(provenance)),
            )
            c.execute(
                "INSERT INTO audit(at,actor,action,subject) VALUES(?,?,?,?)",
                (now, actor, "submit", run_id),
            )
            self.event(
                c,
                run_id,
                "ACCEPTED",
                {"objective": plan.question, "cost_usd": 0, "state": "QUEUED"},
            )
        return self.get(run_id)

    def get(self, run_id):
        with self.connect() as c:
            row = c.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            raise ValueError("RUN_NOT_FOUND")
        out = dict(row)
        with self.connect() as c:
            evidence = c.execute(
                "SELECT payload FROM run_provenance WHERE run_id=?", (run_id,)
            ).fetchone()
        out["acceptance_provenance"] = json.loads(evidence[0]) if evidence else None
        for k in ("plan", "result", "error"):
            out[k] = json.loads(out[k]) if out[k] else None
        return out

    def finish(self, run_id, state, result=None, error=None):
        if state not in TERMINAL:
            raise ValueError("INVALID_TERMINAL_STATE")
        with self.transaction() as c:
            row = c.execute(
                "SELECT state FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if not row or row[0] in TERMINAL:
                return
            c.execute(
                "UPDATE runs SET state=?,finished=?,result=?,error=? WHERE run_id=?",
                (
                    state,
                    time.time(),
                    canonical(result) if result else None,
                    canonical(error) if error else None,
                    run_id,
                ),
            )
            self.event(
                c, run_id, state, {"state": state, "result": result, "error": error}
            )

    def cancel(self, run_id, actor):
        run = self.get(run_id)
        if run["actor"] != actor:
            raise ValueError("FORBIDDEN_OWNER")
        with self.transaction() as c:
            c.execute("UPDATE runs SET cancel=1 WHERE run_id=?", (run_id,))
            c.execute(
                "INSERT INTO audit(at,actor,action,subject) VALUES(?,?,?,?)",
                (time.time(), actor, "cancel_requested", run_id),
            )
        if run["state"] == "QUEUED":
            self.finish(run_id, "CANCELLED")
        return {
            "run_id": run_id,
            "cancellation_requested": True,
            "state": self.get(run_id)["state"],
        }
