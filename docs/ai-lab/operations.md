# Operational evidence and enablement

This increment follows authorized merge #266, main `d3fa8f1a32e2fb3cde94bae45bde106dc3c30dbd`. No official financial state/configuration, workflows, Release assets, champions or broker flags change. It introduces no host, secret, cost or network notification by itself.

## Query actual work

`floor_get_usage(days=7)` provides accepted/terminal counts, states, safe error-code groups, queue/execution samples and nearest-rank p95, durations by recipe, notification states and retry attempts. Window: jobs **accepted** within the trailing 1–90 days; their associated notifications are included even if delivered later. Active-job duration is not mixed with completed timing. Rejected requests are not falsely counted as accepted or audited; full rejection instrumentation remains open. Ordinary readers see their own actor; administrators see aggregate global usage. No actor-selector parameter or arbitrary SQL is exposed.

Cloud estimate is USD 0 for the existing local-only profile. Measured monetary cost is `null`, provider invoice verification is false. Electricity/hardware/Internet/AI plans/maintenance are excluded. Runtime seconds can prioritize optimization, but are not a fabricated invoice.

`floor_get_operational_readiness()` is administrator-only. It checks the control DB and reports volume size/categories, skipped symlinks and explicit unverified remote/Slack/assistant gates. It does not probe sessions, credentials, a Slack workspace or network. It makes no worker-liveness inference. It always leaves platform completion false: a local preflight cannot prove external acceptance.

Operator equivalents on an **existing** isolated research volume:

```bash
PYTHONPATH=src .venv/bin/python -m ai_lab.operations readiness --root /absolute/persistent/floor-lab
PYTHONPATH=src .venv/bin/python -m ai_lab.operations usage --root /absolute/persistent/floor-lab --days 7
```

The CLI refuses an absent control DB. Store initialization applies additive research-schema setup when necessary; queries then read the existing volume. It does not create empty substitute evidence, submit jobs, delete artifacts or contact Slack. Keep preflight output private because global counts and operational metadata can be sensitive.

## Slack receipt semantics

The Web API adapter now requires `ok=true`, a message `ts` of the expected decimal form and an echoed channel matching the configured channel ID. Wrong/missing channel or malformed acknowledgement produces UNKNOWN, preserves experiment success/results and stops automatic retries. Only an explicit `ok=false` rejection is treated as known rejection. Retry-After is bounded and malformed headers fall back to 60 seconds. Retain the existing 1.1-second pacing and known-rejection retry ceiling.

Successful delivery and a minimal receipt are committed in the same SQLite transaction: event ID, channel ID, message timestamp, local acknowledgement timestamp, payload SHA-256 and transport label. No token, response text or dataset is stored in receipts. The response's channel and message ID are sufficient references for operator reconciliation; no extra history/admin scope is added. Thread parents must have a receipt in the same configured channel and transport. A channel change or legacy receipt absence never reuses a foreign/unknown thread.

| Evidence | Meaning | Can close real-delivery KR? |
|---|---|---|
| `slack_web_api_ack` | Validated response from normal adapter; a unit test can still mock its HTTP transport | Only with real deployment/test provenance and actual Slack-message reconciliation |
| `injected_unverified` | Custom sender used, including fixture injection | No |
| Legacy DELIVERED without receipt | Earlier code persisted only ts | No automatic certification; manually reconcile or leave unverified |
| `auth.test` success | Configured token authenticated | No; private-channel delivery still unproven |
| PENDING/RETRY/FAILED/UNKNOWN | Not confirmed delivered | No |

`independently_verified_live_deliveries=null` is intentional. Counting adapter acknowledgements is not proof that fixtures were real network calls. Ten distinct terminal experiment runs, including success/failure/recovery, must be externally verified; ten lifecycle notifications from one run do not satisfy ten E2E executions. Do not publish private channel IDs/ts or full operational receipts in this public repository.

## Schema and rollback

Research control schema marker 3 adds `notification_receipts` with an event FK; migration is additive/idempotent through the existing Store initializer. Existing jobs, idempotency keys, payloads and legacy outbox states stay intact. It does not rewrite official FLOOR DB schemas. Tests cover repeated v2 upgrade, isolation, failure-state independence, ambiguous acknowledgement blocking, channel change and receipt-preserving backup/restore. Stop MCP/imports/worker and back up the research volume before enabling the new version. Roll back source with worker stopped and restore the previous volume checkpoint if necessary; retain receipt evidence separately. Do not downgrade MLflow in place.

## Remaining enablement

1. Authorized administrator selects persistent host/private tunnel and installs independent supervisor. TLS/auth/public OAuth review depends on chosen route; no public deployment is authorized implicitly.
2. Authorized Slack administrator selects workspace/private channel, installs least-privilege App (`chat:write`), invites bot and injects secrets outside Git/prompts.
3. Check auth, run controlled jobs, reconcile message channel+ts against real Slack and retain restricted evidence for ten distinct E2Es. Recover known failures without rerunning computation; reconcile UNKNOWN before resending.
4. Verify two actual assistant accounts/clients, secure artifact hosting, full transitive environment lock, cost/quota/retention acceptance. Inspector plus Python protocol clients do not substitute for actual Codex/Claude account evidence.

Official contracts rechecked for this increment: [chat.postMessage](https://docs.slack.dev/reference/methods/chat.postMessage/) and [rate limits](https://docs.slack.dev/apis/web-api/rate-limits/).
