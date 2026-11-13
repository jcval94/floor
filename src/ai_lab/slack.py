"""Slack Web API outbox. No integration success is inferred from a mock."""

import json
import os
import time
import urllib.error
import urllib.request
from ai_lab.store import canonical


class SlackDeliveryError(Exception):
    def __init__(self, code, retry_after=0, ambiguous=False):
        self.code, self.retry_after, self.ambiguous = code, retry_after, ambiguous


def post_message(token, channel, event, thread_ts=None):
    data = json.loads(event["payload"])
    result = data.get("result") or {}
    lines = [f"FLOOR AI LAB — {event['kind']}", f"Run: {event['run_id']}"]
    if data.get("objective"):
        lines.append(data["objective"])
    for m in result.get("metrics", []):
        lines.append(
            f"{m['model']} / {m['variant']}: MAE={m['mae_floor_pct']:.6f}; RMSE={m['rmse_floor_pct']:.6f}; fold std={m['fold_mae_std']:.6f}"
        )
    if result:
        lines.extend(
            [
                result.get("conclusion", ""),
                f"Dataset: {result.get('dataset_id')}",
                f"Duration: {result.get('duration_seconds')} s; local cloud cost: $0",
                f"Report relative to operator's protected artifact root: {result.get('report')}",
            ]
        )
    if data.get("error"):
        lines.append("Error: " + canonical(data["error"]))
    payload = {
        "channel": channel,
        "text": "\n".join(lines)[:12000],
        "client_msg_id": event["event_id"],
        "unfurl_links": False,
        "unfurl_media": False,
    }
    if thread_ts:
        payload["thread_ts"] = thread_ts
    request = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=canonical(payload).encode(),
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            result = json.load(response)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise SlackDeliveryError(
                "rate_limited",
                min(3600, max(1, int(e.headers.get("Retry-After", "60")))),
            ) from None
        raise SlackDeliveryError(
            "http_" + str(e.code), 60 if e.code >= 500 else 0, ambiguous=e.code >= 500
        ) from None
    except (OSError, ValueError):
        raise SlackDeliveryError("delivery_unknown", ambiguous=True) from None
    if not result.get("ok") or not result.get("ts"):
        raise SlackDeliveryError(
            "slack_rejected", 0
        )  # never log untrusted response containing secrets
    return result["ts"]


def drain(store, sender=post_message):
    token, channel = (
        os.environ.get("FLOOR_SLACK_BOT_TOKEN"),
        os.environ.get("FLOOR_SLACK_CHANNEL_ID"),
    )
    if not token or not channel:
        return {"state": "WAITING_CONFIG", "delivered": 0}
    with store.transaction() as c:
        # Single supervised worker owns outbox. Crash after remote acceptance is ambiguous.
        c.execute(
            "UPDATE outbox SET state='UNKNOWN',error='worker_interrupted_delivery' WHERE state='DELIVERING'"
        )
        row = c.execute(
            "SELECT * FROM outbox WHERE state IN ('PENDING','RETRY') AND next_at<=? ORDER BY rowid LIMIT 1",
            (time.time(),),
        ).fetchone()
        if not row:
            return {"state": "IDLE", "delivered": 0}
        event = dict(row)
        c.execute(
            "UPDATE outbox SET state='DELIVERING',attempts=attempts+1 WHERE event_id=?",
            (event["event_id"],),
        )
        parent = c.execute(
            "SELECT slack_ts FROM outbox WHERE run_id=? AND kind='ACCEPTED' AND state='DELIVERED'",
            (event["run_id"],),
        ).fetchone()
    try:
        ts = sender(token, channel, event, parent[0] if parent else None)
    except SlackDeliveryError as exc:
        state = (
            "UNKNOWN"
            if exc.ambiguous
            else ("RETRY" if exc.retry_after and event["attempts"] < 4 else "FAILED")
        )
        with store.transaction() as c:
            c.execute(
                "UPDATE outbox SET state=?,error=?,next_at=? WHERE event_id=?",
                (
                    state,
                    exc.code,
                    time.time() + max(exc.retry_after, 2 ** event["attempts"]),
                    event["event_id"],
                ),
            )
        return {"state": state, "delivered": 0}
    with store.transaction() as c:
        c.execute(
            "UPDATE outbox SET state='DELIVERED',slack_ts=?,error=NULL WHERE event_id=?",
            (ts, event["event_id"]),
        )
        # Global channel pacing, including replay backlog.
        c.execute(
            "UPDATE outbox SET next_at=max(next_at,?) WHERE state IN ('PENDING','RETRY')",
            (time.time() + 1.1,),
        )
    return {"state": "DELIVERED", "delivered": 1, "slack_ts": ts}


def test_connection():
    token = os.environ.get("FLOOR_SLACK_BOT_TOKEN")
    channel = os.environ.get("FLOOR_SLACK_CHANNEL_ID")
    if not token or not channel:
        raise ValueError("SLACK_NOT_CONFIGURED")
    request = urllib.request.Request(
        "https://slack.com/api/auth.test",
        data=b"{}",
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            result = json.load(response)
    except (OSError, ValueError):
        raise ValueError("SLACK_CONNECTION_FAILED") from None
    if not result.get("ok"):
        raise ValueError("SLACK_AUTH_REJECTED")
    return {
        "authenticated": True,
        "channel_configured": True,
        "delivery_verified": False,
        "team_id": result.get("team_id"),
        "bot_id": result.get("bot_id"),
    }
