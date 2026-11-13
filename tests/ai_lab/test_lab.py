"""Research extras are opt-in; default operational CI does not install an MCP stack."""

from pathlib import Path
import sys
from datetime import datetime, timedelta, timezone
import pytest

pytest.importorskip("mcp")
pytest.importorskip("sklearn")
from ai_lab.contracts import Plan
from ai_lab.store import Store
from ai_lab.recipes import evaluate
from ai_lab.server import build_server, principal, PrivateHTTPAuth
from ai_lab.slack import drain, SlackDeliveryError


@pytest.fixture
def registered(tmp_path):
    store = Store(tmp_path)
    rows = []
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    for i in range(160):
        ts = (start + timedelta(days=i)).isoformat()
        rows.append(
            {
                "symbol": "SYNTHETIC",
                "timestamp": ts,
                "available_at": ts,
                "target_end_date_w1": (start + timedelta(days=i + 5)).isoformat(),
                "horizon_complete_w1": True,
                "close": 100.0,
                "floor_w1": 98.0 - (i % 7) / 10,
                "momentum_20": (i % 7) / 100,
                "ret_lag_1": (i % 3) / 100,
                "feature_available_at": {"momentum_20": ts, "ret_lag_1": ts},
            }
        )
    manifest = {
        "target": "floor_w1",
        "horizon_sessions": 5,
        "features": ["momentum_20", "ret_lag_1"],
        "temporal_evidence": "synthetic_fixture",
        "source": "test fixture",
    }
    dataset = store.register(rows, manifest)
    plan = Plan(
        question="Compara tres modelos sintéticos",
        recipe="model_comparison",
        dataset_id=dataset,
        features=manifest["features"],
    )
    return store, rows, manifest, plan


def test_idempotence_and_conflict(registered):
    store, _, _, p = registered
    a = store.submit(p, "alice", "same")
    b = store.submit(p, "alice", "same")
    assert a["run_id"] == b["run_id"]
    with pytest.raises(ValueError, match="IDEMPOTENCY_CONFLICT"):
        store.submit(p.model_copy(update={"seed": 43}), "alice", "same")
    assert len(list(store.root.glob("runs/*"))) == 0  # never trains in request


def test_temporal_leakage_and_hash(registered):
    store, rows, m, p = registered
    rows[0]["feature_available_at"]["ret_lag_1"] = rows[-1]["timestamp"]
    bad = store.register(rows, m)
    with pytest.raises(ValueError, match="FEATURE_LEAKAGE"):
        store.validate(p.model_copy(update={"dataset_id": bad}))
    (store.root / "datasets" / p.dataset_id / "rows.json").write_text("[]")
    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        store.validate(p)


def test_three_recipes_reproducible(registered):
    _, rows, m, p = registered
    for recipe in ("model_comparison", "feature_audit", "feature_ablation"):
        spec = p.model_copy(
            update={
                "recipe": recipe,
                "drop_features": ["ret_lag_1"] if recipe == "feature_ablation" else [],
            }
        )
        a, b = evaluate(rows, m, spec), evaluate(rows, m, spec)
        assert a["metrics"] == b["metrics"]
        assert a["predictions"] == b["predictions"]
        assert all(r["train_end"] < r["test_start"] for r in a["folds"])
        assert all(r["oos_rows"] > 0 for r in a["folds"])
        if recipe == "feature_audit":
            assert len(a["feature_audit"]) == 2
        if recipe == "feature_ablation":
            assert {x["variant"] for x in a["metrics"]} == {"full", "ablated"}


def test_quota_and_cancel(registered):
    s, _, _, p = registered
    for i in range(5):
        s.submit(p, "alice", str(i))
    with pytest.raises(ValueError, match="BLOCKED_QUOTA"):
        s.submit(p, "alice", "sixth")
    run = s.submit(p, "bob", "one")
    with pytest.raises(ValueError, match="FORBIDDEN"):
        s.cancel(run["run_id"], "eve")
    assert s.cancel(run["run_id"], "bob")["state"] == "CANCELLED"


def test_notification_independent_retry_no_reexecution(registered, monkeypatch):
    s, _, _, p = registered
    run = s.submit(p, "alice", "one")
    s.finish(run["run_id"], "SUCCEEDED", result={"measured": True})
    assert drain(s)["state"] == "WAITING_CONFIG"
    monkeypatch.setenv("FLOOR_SLACK_BOT_TOKEN", "test-token-only")
    monkeypatch.setenv("FLOOR_SLACK_CHANNEL_ID", "test-channel-only")

    def failure(*args):
        raise SlackDeliveryError("rate_limited", 1)

    assert drain(s, sender=failure)["state"] == "RETRY"
    assert s.get(run["run_id"])["state"] == "SUCCEEDED"
    with s.transaction() as c:
        c.execute("UPDATE outbox SET next_at=0")
    assert drain(s, sender=lambda *args: "fixture-ts")["slack_ts"] == "fixture-ts"
    with s.connect() as c:
        assert (
            c.execute("SELECT count(*) FROM outbox WHERE state='DELIVERED'").fetchone()[
                0
            ]
            == 1
        )
        assert c.execute("SELECT count(*) FROM runs").fetchone()[0] == 1


def test_private_http_denies_without_auth(registered):
    import asyncio

    s, _, _, _ = registered
    messages = []

    async def target(*args):
        raise AssertionError("should not reach MCP")

    async def send(message):
        messages.append(message)

    async def receive():
        return {"type": "http.request", "body": b""}

    app = PrivateHTTPAuth(target, {})
    asyncio.run(app({"type": "http", "headers": []}, receive, send))
    assert messages[0]["status"] == 401


def test_mcp_role_pagination_and_novel_request(registered):
    import asyncio

    s, _, _, p = registered
    mcp = build_server(s)

    async def scenario():
        token = principal.set({"actor": "readonly", "role": "read_only"})
        try:
            listing = await mcp.list_tools()
            assert len(listing) >= 15
            raw = await mcp.call_tool(
                "floor_submit_experiment",
                {"plan": p.model_dump(), "idempotency_key": "denied"},
            )
            assert "FORBIDDEN_ROLE" in str(raw)
            raw = await mcp.call_tool("floor_get_data_catalog", {"limit": 1000})
            assert "INVALID_PAGINATION" in str(raw)
            raw = await mcp.call_tool(
                "floor_plan_experiment",
                {
                    "question": "Prueba si VIX mejora forecast",
                    "dataset_id": p.dataset_id,
                },
            )
            assert "AWAITING_APPROVAL" in str(raw)
        finally:
            principal.reset(token)

    asyncio.run(scenario())


def test_worker_real_process_and_tracking(registered):
    pytest.importorskip("mlflow")
    pytest.importorskip("sqlalchemy")
    import os
    import subprocess

    s, _, _, p = registered
    run = s.submit(p, "alice", "process")
    env = {
        **os.environ,
        "PYTHONPATH": str(Path("src").resolve()),
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
    }
    completed = subprocess.run(
        [sys.executable, "-m", "ai_lab.worker", "--root", str(s.root), "--once"],
        env=env,
        timeout=120,
        capture_output=True,
    )
    assert completed.returncode == 0, completed.stderr.decode()
    result = s.get(run["run_id"])
    assert result["state"] == "SUCCEEDED", result
    assert result["result"]["mlflow_run_id"]
    assert (s.root / "runs" / run["run_id"] / "report.html").exists()


def test_controlled_corruption_fails_worker(registered):
    from ai_lab.worker import tick

    s, _, _, p = registered
    run = s.submit(p, "alice", "corrupt-after-acceptance")
    (s.root / "datasets" / p.dataset_id / "rows.json").write_text("[]")
    tick(s)
    assert s.get(run["run_id"])["state"] == "FAILED"
    assert s.get(run["run_id"])["error"] is not None
    with s.connect() as c:
        assert (
            c.execute("SELECT count(*) FROM outbox WHERE kind='FAILED'").fetchone()[0]
            == 1
        )


def test_retention_protects_evidence_and_never_deletes(registered):
    from ai_lab.retention import dry_run
    import time

    s, _, _, p = registered
    run = s.submit(p, "alice", "retention")
    s.finish(run["run_id"], "FAILED", error={"code": "TEST"})
    directory = s.root / "runs" / run["run_id"]
    directory.mkdir(parents=True)
    (directory / "report.html").write_text("test")
    (directory / "provenance.json").write_text("{}")
    report = dry_run(s, time.time() + 40 * 86400)
    assert report["projected_bytes"] == 4
    assert len(report["protected_files"]) == 1
    assert report["deletion_implemented"] is False
    assert (directory / "report.html").exists()


def test_concurrent_duplicate_submission(registered):
    from concurrent.futures import ThreadPoolExecutor

    s, _, _, p = registered
    with ThreadPoolExecutor(max_workers=4) as pool:
        runs = list(pool.map(lambda _: s.submit(p, "alice", "concurrent"), range(4)))
    assert len({r["run_id"] for r in runs}) == 1
    with s.connect() as c:
        assert c.execute("SELECT count(*) FROM outbox").fetchone()[0] == 1


def test_runtime_timeout_and_cancellation(registered, monkeypatch):
    import subprocess
    import threading
    import time
    from ai_lab.worker import tick

    s, _, _, p = registered
    # Controlled real child process, replacing only the registered computation.
    # Exercises actual process-group termination without making trading claims.
    original = subprocess.Popen

    def sleeping_child(command, **kwargs):
        if "ai_lab.execute" in command:
            return original(
                [sys.executable, "-c", "import time; time.sleep(30)"], **kwargs
            )
        return original(command, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", sleeping_child)
    spec = p.model_copy(update={"max_seconds": 5})
    run = s.submit(spec, "alice", "timeout")
    tick(s)
    assert s.get(run["run_id"])["state"] == "TIMED_OUT"
    run = s.submit(p, "alice", "cancel-running")

    def cancel_later():
        time.sleep(0.3)
        s.cancel(run["run_id"], "alice")

    thread = threading.Thread(target=cancel_later)
    thread.start()
    tick(s)
    thread.join()
    assert s.get(run["run_id"])["state"] == "CANCELLED"


def test_streamable_http_real_auth_and_tools(registered, monkeypatch):
    import asyncio
    import hashlib
    import json
    import os
    import socket
    import subprocess
    import time
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    for key in (
        "ALL_PROXY",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "all_proxy",
        "http_proxy",
        "https_proxy",
    ):
        monkeypatch.delenv(key, raising=False)
    s, _, _, _ = registered
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    # Public test fixture value, never a deployed/account credential.
    test_bearer = "synthetic-auth-fixture-not-a-real-credential"
    env = {
        **os.environ,
        "PYTHONPATH": str(Path("src").resolve()),
        "FLOOR_MCP_PORT": str(port),
        "FLOOR_LAB_TOKEN_HASHES": json.dumps(
            {
                hashlib.sha256(test_bearer.encode()).hexdigest(): {
                    "actor": "http-test",
                    "role": "read_only",
                }
            }
        ),
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "ai_lab.server",
            "--root",
            str(s.root),
            "--transport",
            "streamable-http",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    url = f"http://127.0.0.1:{port}/mcp"
    try:
        for _ in range(100):
            try:
                response = httpx.post(url, timeout=0.2)
                break
            except httpx.ConnectError:
                time.sleep(0.05)
        else:
            raise AssertionError("HTTP server failed to start")
        assert response.status_code == 401
        response = httpx.post(
            url,
            headers={
                "Authorization": "Bearer " + test_bearer,
                "Origin": "https://attacker.example",
            },
            timeout=5,
        )
        assert response.status_code in (400, 403)

        async def connected():
            async with streamablehttp_client(
                url, headers={"Authorization": "Bearer " + test_bearer}
            ) as (read, write, _):
                async with ClientSession(read, write) as session:
                    init = await session.initialize()
                    assert init.protocolVersion == "2025-11-25"
                    listing = await session.list_tools()
                    assert len(listing.tools) >= 20
                    result = await session.call_tool("floor_get_status", {})
                    assert result.structuredContent["ok"] is True

        asyncio.run(connected())
    finally:
        process.terminate()
        process.wait(timeout=10)


def test_snapshot_import_excludes_partial_bar_and_rejects_gaps(tmp_path):
    xc = pytest.importorskip("exchange_calendars")
    import sqlite3
    from ai_lab.import_snapshot import import_market

    cal = xc.get_calendar("XNYS")
    sessions = cal.sessions_in_range("2024-01-02", "2024-09-01")
    db = tmp_path / "source.sqlite"
    with sqlite3.connect(db) as c:
        c.execute(
            "CREATE TABLE daily_bars(symbol TEXT,ts_utc TEXT,open REAL,high REAL,low REAL,close REAL,volume REAL,fetched_at_utc TEXT)"
        )
        for symbol in ("SPY", "GOOGL", "MSFT"):
            for i, day in enumerate(sessions):
                # The last candle was fetched at the session open: incomplete.
                fetched = (
                    cal.session_open(day).isoformat()
                    if i == len(sessions) - 1
                    else "2026-11-13T00:00:00+00:00"
                )
                c.execute(
                    "INSERT INTO daily_bars VALUES(?,?,?,?,?,?,?,?)",
                    (
                        symbol,
                        cal.session_open(day).isoformat(),
                        100 + i,
                        102 + i,
                        98 + i,
                        101 + i,
                        1000.0,
                        fetched,
                    ),
                )
    store = Store(tmp_path / "lab")
    dataset = import_market(store.root, db, "synthetic-archive-fixture", "fixture-run")
    rows, manifest = store.dataset(dataset)
    assert len(manifest["excluded_incomplete_bars"]) == 3
    assert max(r["target_end_date_w1"][:10] for r in rows) < str(sessions[-1].date())
    assert all(r["timestamp"][11:16] == "23:59" for r in rows)
    with sqlite3.connect(db) as c:
        c.execute(
            "DELETE FROM daily_bars WHERE symbol=? AND ts_utc=?",
            ("SPY", cal.session_open(sessions[50]).isoformat()),
        )
    with pytest.raises(ValueError, match="MISSING_OR_DUPLICATE_MARKET_SESSIONS"):
        import_market(
            tmp_path / "lab-gap", db, "synthetic-archive-fixture", "fixture-run"
        )


def test_two_research_restores_and_tamper_detection(registered, tmp_path):
    from ai_lab.backup import backup, restore

    s, _, _, p = registered
    run = s.submit(p, "alice", "backup")
    s.finish(run["run_id"], "FAILED", error={"code": "CONTROLLED"})
    destination = tmp_path.parent / (tmp_path.name + "-backup")
    backup(s, destination)
    for i in range(2):
        restored = restore(
            destination, tmp_path.parent / (tmp_path.name + "-restored-" + str(i))
        )
        assert restored.get(run["run_id"])["state"] == "FAILED"
        assert (
            restored.dataset(p.dataset_id)[1]["temporal_evidence"]
            == "synthetic_fixture"
        )
    (destination / "datasets" / p.dataset_id / "rows.json").write_text("[]")
    with pytest.raises(ValueError, match="BACKUP_HASH_MISMATCH"):
        restore(destination, tmp_path.parent / (tmp_path.name + "-tampered-restore"))


def test_failed_run_retains_acceptance_provenance(registered):
    from ai_lab.worker import tick

    s, _, _, p = registered
    run = s.submit(p, "alice", "failure-provenance")
    accepted = s.get(run["run_id"])["acceptance_provenance"]
    assert accepted["git_commit"]
    assert accepted["dataset_manifest"]["rows_hash"]
    (s.root / "datasets" / p.dataset_id / "rows.json").write_text("[]")
    tick(s)
    assert s.get(run["run_id"])["state"] == "FAILED"
    assert s.get(run["run_id"])["acceptance_provenance"] == accepted


def test_worker_blocks_code_change_after_acceptance(registered, monkeypatch):
    import ai_lab.provenance as provenance
    from ai_lab.worker import tick

    s, _, _, p = registered
    run = s.submit(p, "alice", "changed-code")
    observed = provenance.code_identity()
    monkeypatch.setattr(
        provenance,
        "code_identity",
        lambda: {**observed, "git_commit": "changed-after-acceptance"},
    )
    tick(s)
    result = s.get(run["run_id"])
    assert result["state"] == "FAILED"
    assert result["error"]["code"] == "CODE_OR_ENV_CHANGED_AFTER_ACCEPTANCE"
    assert not (s.root / "runs" / run["run_id"] / "completion.json").exists()
