"""Operational fault fixtures; none of these acknowledgements are live Slack evidence."""

import asyncio
import io
import json
import urllib.error

import pytest

pytest.importorskip("mcp")

from ai_lab.backup import backup, restore
from ai_lab.operations import readiness, usage
from ai_lab.server import build_server, principal
from ai_lab.slack import drain, post_message, SlackAcknowledgement, SlackDeliveryError
from ai_lab.store import Store, canonical, digest


@pytest.fixture
def lab(tmp_path):
    s = Store(tmp_path / "lab")
    for name, actor, state, created, started, finished, error in (
        ("alice-success", "alice", "SUCCEEDED", 900, 910, 930, None),
        ("bob-failure", "bob", "FAILED", 940, 950, 970, {"code": "BOB_PRIVATE"}),
        ("alice-active", "alice", "RUNNING", 980, 990, None, None),
        ("old", "alice", "FAILED", -9000000, -8999999, -8999998, {"code": "OLD_ERROR"}),
    ):
        with s.transaction() as c:
            c.execute(
                "INSERT INTO runs(run_id,experiment_id,actor,idem,plan,state,created,started,finished,error) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    name,
                    name,
                    actor,
                    name,
                    canonical({"recipe": "model_comparison"}),
                    state,
                    created,
                    started,
                    finished,
                    canonical(error) if error else None,
                ),
            )
            s.event(c, name, "ACCEPTED", {})
    return s


def test_usage_owner_window_timing_and_no_billing_claim(lab):
    result = usage(lab, "alice", 1, now=1000)
    assert result["accepted_runs"] == 2
    assert result["states"] == {"SUCCEEDED": 1, "RUNNING": 1}
    assert result["errors_by_code"] == {}
    assert result["queue_time"]["p95_seconds"] == 10
    assert result["execution_time"]["samples"] == 1
    assert result["execution_time"]["total_seconds"] == 20
    assert result["cost"]["measured_monetary_cost_usd"] is None
    all_users = usage(lab, None, 1, now=1000)
    assert all_users["accepted_runs"] == 3
    assert all_users["errors_by_code"] == {"BOB_PRIVATE": 1}
    assert all_users["notifications"]["api_acknowledgements"] == 0
    for days in (0, 91, True, 1.5):
        with pytest.raises(ValueError, match="INVALID_USAGE_WINDOW"):
            usage(lab, "alice", days, now=1000)


def test_mcp_usage_cannot_select_other_actor_and_readiness_is_admin(lab):
    async def scenario():
        server = build_server(lab)
        token = principal.set({"actor": "alice", "role": "read_only"})
        try:
            raw = await server.call_tool("floor_get_usage", {"days": 90})
            assert "BOB_PRIVATE" not in str(raw)
            assert "FORBIDDEN_ROLE" in str(
                await server.call_tool("floor_get_operational_readiness", {})
            )
            assert "INVALID_USAGE_WINDOW" in str(
                await server.call_tool("floor_get_usage", {"days": 91})
            )
        finally:
            principal.reset(token)
        token = principal.set({"actor": "admin", "role": "administrator"})
        try:
            assert "control_db_integrity_ok" in str(
                await server.call_tool("floor_get_operational_readiness", {})
            )
        finally:
            principal.reset(token)

    asyncio.run(scenario())


def test_offline_preflight_never_leaks_secrets_or_follows_links(
    lab, tmp_path, monkeypatch
):
    monkeypatch.setenv("FLOOR_SLACK_BOT_TOKEN", "super-secret-fixture")
    monkeypatch.setenv("FLOOR_SLACK_CHANNEL_ID", "private-channel-fixture")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_bytes(b"x" * 100000)
    (lab.root / "linked").symlink_to(outside, target_is_directory=True)
    report = readiness(lab)
    assert report["slack_configuration_present"] is True
    assert report["storage"]["symlinks_skipped"] == 1
    assert "RESEARCH_VOLUME_SYMLINKS" in report["blockers"]
    assert report["network_requests"] == report["messages_sent"] == 0
    assert not report["platform_complete"]
    assert "super-secret" not in canonical(
        report
    ) and "private-channel-fixture" not in canonical(report)
    assert set(report["storage"]["by_category"]) == {"metadata"}


def fake_response(monkeypatch, payload):
    import ai_lab.slack as slack

    captured = []

    def send(request, timeout):
        captured.append(json.loads(request.data))
        return io.BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr(slack.urllib.request, "urlopen", send)
    return captured


@pytest.mark.parametrize(
    "response",
    [
        {"ok": True, "ts": "1700000000.000001", "channel": "wrong-channel"},
        {"ok": True, "ts": "not-a-ts", "channel": "C123"},
        {"ok": True, "ts": "1700000000.000001"},
        [],
    ],
)
def test_ambiguous_ack_never_becomes_delivered(lab, monkeypatch, response):
    fake_response(monkeypatch, response)
    monkeypatch.setenv("FLOOR_SLACK_BOT_TOKEN", "fixture-secret")
    monkeypatch.setenv("FLOOR_SLACK_CHANNEL_ID", "C123")
    assert drain(lab)["state"] == "UNKNOWN"
    with lab.connect() as c:
        assert (
            c.execute("SELECT count(*) FROM notification_receipts").fetchone()[0] == 0
        )
    assert lab.get("alice-success")["state"] == "SUCCEEDED"


def test_receipts_are_channel_scoped_and_survive_restore(lab, tmp_path, monkeypatch):
    captured = fake_response(
        monkeypatch, {"ok": True, "ts": "1700000000.000001", "channel": "C123"}
    )
    monkeypatch.setenv("FLOOR_SLACK_BOT_TOKEN", "fixture-secret")
    monkeypatch.setenv("FLOOR_SLACK_CHANNEL_ID", "C123")
    assert drain(lab)["state"] == "DELIVERED"
    with lab.connect() as c:
        receipt = dict(c.execute("SELECT * FROM notification_receipts").fetchone())
    assert receipt["channel_id"] == "C123"
    assert receipt["payload_sha256"] == digest(b"{}")
    assert (
        receipt["transport"] == "slack_web_api_ack"
    )  # mocked transport; not live evidence
    backup(lab, tmp_path / "backup")
    restored = restore(tmp_path / "backup", tmp_path / "restored")
    with restored.connect() as c:
        assert (
            dict(c.execute("SELECT * FROM notification_receipts").fetchone()) == receipt
        )
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    # Deliver a later event first in queue, same channel: parent is retained.
    with lab.transaction() as c:
        c.execute("UPDATE outbox SET next_at=9999999999 WHERE run_id!='alice-success'")
        lab.event(c, "alice-success", "RUNNING", {})
    assert drain(lab)["state"] == "DELIVERED"
    assert captured[-1]["thread_ts"] == "1700000000.000001"
    with lab.transaction() as c:
        lab.event(c, "alice-success", "SUCCEEDED", {})
    monkeypatch.setenv("FLOOR_SLACK_CHANNEL_ID", "C456")
    captured = fake_response(
        monkeypatch, {"ok": True, "ts": "1700000001.000001", "channel": "C456"}
    )
    assert drain(lab)["state"] == "DELIVERED"
    assert "thread_ts" not in captured[-1]


def test_injected_and_legacy_delivery_cannot_count_as_api_receipts(lab, monkeypatch):
    monkeypatch.setenv("FLOOR_SLACK_BOT_TOKEN", "fixture-secret")
    monkeypatch.setenv("FLOOR_SLACK_CHANNEL_ID", "C123")
    assert drain(lab, sender=lambda *args: "fixture-ts")["state"] == "DELIVERED"
    with lab.transaction() as c:
        c.execute(
            "UPDATE outbox SET state='DELIVERED',slack_ts='legacy-fixture' WHERE run_id='bob-failure'"
        )
    report = usage(lab, None, 1, now=1000)["notifications"]
    assert report["api_acknowledgements"] == 0
    assert report["injected_receipts"] == 1
    assert report["legacy_delivered_without_receipt"] == 1
    assert report["independently_verified_live_deliveries"] is None


def test_migration_v2_is_repeatable_and_preserves_existing_runs(lab):
    with lab.transaction() as c:
        c.execute("DROP TABLE notification_receipts")
        c.execute("DELETE FROM schema_version WHERE version=3")
    for _ in range(2):
        lab = Store(lab.root)
    with lab.connect() as c:
        assert [
            r[0]
            for r in c.execute("SELECT version FROM schema_version ORDER BY version")
        ] == [1, 2, 3]
        assert c.execute("SELECT count(*) FROM runs").fetchone()[0] == 4


def test_malformed_rate_limit_header_is_bounded(monkeypatch):
    import ai_lab.slack as slack

    def send(*args, **kwargs):
        raise urllib.error.HTTPError(
            "https://slack.com/api/chat.postMessage",
            429,
            "limited",
            {"Retry-After": "invalid"},
            None,
        )

    monkeypatch.setattr(slack.urllib.request, "urlopen", send)
    with pytest.raises(SlackDeliveryError) as error:
        post_message(
            "fixture",
            "C123",
            {
                "payload": "{}",
                "kind": "FAILED",
                "run_id": "fixture",
                "event_id": "fixture",
            },
        )
    assert error.value.retry_after == 60 and error.value.ambiguous is False


def test_valid_ack_remains_string_compatible(monkeypatch):
    fake_response(
        monkeypatch, {"ok": True, "ts": "1700000000.000001", "channel": "C123"}
    )
    ack = post_message(
        "fixture",
        "C123",
        {"payload": "{}", "kind": "FAILED", "run_id": "fixture", "event_id": "fixture"},
    )
    assert isinstance(ack, SlackAcknowledgement) and ack == "1700000000.000001"
    assert ack.channel == "C123"


def test_explicit_rejection_is_failed_without_touching_job(lab, monkeypatch):
    fake_response(monkeypatch, {"ok": False, "error": "fixture-rejection"})
    monkeypatch.setenv("FLOOR_SLACK_BOT_TOKEN", "fixture-secret")
    monkeypatch.setenv("FLOOR_SLACK_CHANNEL_ID", "C123")
    assert drain(lab)["state"] == "FAILED"
    assert lab.get("alice-success")["state"] == "SUCCEEDED"
    with lab.connect() as c:
        assert (
            c.execute("SELECT count(*) FROM notification_receipts").fetchone()[0] == 0
        )
