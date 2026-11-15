# Closing OKR #261: evidence, owners and enablement

Checkpoint: authorized merge [#267](https://github.com/jcval94/floor/pull/267), main `f7975f96792b2e721083d102c76538cce1fa2e23`; [main CI passed](https://github.com/jcval94/floor/actions/runs/43735894149). Earlier local code evidence does not close deployed acceptance. The issue remains OPEN.

## What JC needs to provide

Only two initial environment facts are needed; implementation details remain engineering decisions:

1. **Slack:** which personal/FLOOR workspace to use and whether JC can install/approve an App there. If none exists, say so; no workspace/channel is assumed. Recommended channel name is `floor-experiments`, private. An administrator will need to authorize App installation, invite its bot and place the token in the selected host's secret environment. **Do not paste tokens here, in Git, an issue, a PR or screenshots.** A connected ChatGPT Slack reader would not configure the independent worker's bot or durable delivery by itself.
2. **Persistent host:** which existing operator-owned machine is available, its OS and whether it can remain powered/networked while experiments run. Linux is the currently supported worker platform (POSIX flock/resource/prctl). Windows/WSL2 or macOS requires validation/adaptation; no untested support is claimed. A sleeping/offline PC cannot provide autonomous availability. If there is no suitable existing host, explicitly review a different hosting profile before claiming cost or availability.

Later, JC must open/approve two actual compatible assistant clients for connection acceptance. Engineering can generate their configurations, but protocol SDK/Inspector tests cannot stand in for those account/product sessions. We will choose available clients after the host is known; ChatGPT connectivity remains contingent on its actual supported auth/product capabilities.

No authorization to create credentials, public endpoints, cloud billing, official financial changes or new service accounts is inferred from this checklist. Code/configuration is prepared before such account actions. Current recommendation: existing private host and reviewed secure access; no paid cloud fallback, no online feature-store service and no permanent MLflow server. Training remains on demand. Hardware/electricity/Internet/AI plans are separate from a USD0 incremental-cloud estimate.

## Minimum Slack App preparation

A ready-to-paste [manifest](deploy/slack-app-manifest.json) requests only bot `chat:write`. It adds no history/admin/public-channel scopes, event subscriptions, Socket Mode or inbound endpoint. Local JSON syntax and intended scope are checked; Slack has not validated or installed it.

After workspace authorization:

1. Visit [Slack apps](https://api.slack.com/apps), choose Create New App and the manifest option, select the agreed workspace, paste the JSON and review the requested permission before creation/installation. UI labels may vary. Official [manifest instructions](https://docs.slack.dev/app-manifests/configuring-apps-with-app-manifests/) and [schema](https://docs.slack.dev/reference/app-manifest/) were checked for this preparation.
2. An authorized administrator installs the App, selects/creates a private channel and invites the bot. No private channel is discovered, created or messaged by this repository file.
3. Administrator configures `FLOOR_SLACK_BOT_TOKEN` and `FLOOR_SLACK_CHANNEL_ID` on the worker host using protected secret storage. No token value belongs in a client prompt or public file. Confirm scope/channel membership locally; `auth.test` alone does not establish message delivery.
4. After intentional test-job submission, reconcile each terminal API receipt's channel/message ts with the actual Slack message. Preserve protected evidence outside this public repository. Do not infer ten E2E runs from ten lifecycle notifications or mocks.

Full error/UNKNOWN/retry/thread handling is in [Slack runbook](slack.md) and [operations](operations.md). These preparation steps have not been executed in Slack.

## Acceptance gates and owners

| Gate | Evidence required | Current status | Owner / dependency |
|---|---|---|---|
| Persistent independent execution | Authorized host, immutable source release, persistent volume, supervisor/restart and client-disconnect test on that actual host | Separate worker proved locally; not installed on a durable operator host | JC identifies/authorizes host; engineering installs/prepares and verifies |
| Remote MCP and real clients | HTTPS/private access with appropriate auth, secure artifacts, two actual clients and recorded negotiated versions/permissions | Loopback/stdio + Python SDK/Inspector proven; no remotely available service or actual assistant-account proof | Host/account enablement, then engineering E2E |
| Domain MCP coverage and freshness | Authoritative forecast/market/league queries, Finder results when available or explicit NOT_AVAILABLE, as-of/provenance/stale checks and bounded pagination | Research tools exist; the full operational domain adapter set in KR4.0 is not implemented or validated | Engineering, reusing domain services and contracts from #255/#258/#259 |
| Slack P0 | Ten distinct terminal experiment E2Es reconciled to real messages, including success, failure, delivery recovery and dedup | **0/10 verified live executions**; real API code and mocked failure tests exist | JC/admin authorizes workspace/App/channel/secret; engineering tests and records |
| Portable reproducibility | Full direct+transitive locked environment or reproducible image and clean-install/reconstruction evidence | Direct pins and observed critical dependency versions only; two local restores already reproduce results | Engineering; no account input needed |
| Schema/catalog drift | Columns/keys/index definitions/views/triggers checked against audited snapshot schema; changes block without rewriting DB | Implemented in this follow-up, tested on fixtures and two hash-verified source copies | Engineering; not operational drift CI deployment |
| Full data catalog and temporal/domain integrity | Critical external resources, logical FK/identity rules, versioned schema/migration plan, actual quality/freshness/provenance checks | Physical eight-table snapshot catalog exists; complete external/logical/operational acceptance remains open | Engineering, coordinated with #255/#259 |
| Official observation window | At least ten eligible sessions showing zero new duplicate business keys/orphans for the entities specified in #261 | Not demonstrated. Historical snapshot checks or green CI are not a substitute for this operational observation | Depends on governed official-state/EOD work; #253/#255/#259 remain open |
| Costs/quotas/retention | Measured selected-profile usage/cost, fail-closed quotas, retention dry-run + restore + approved first cleanup | Local quotas/dry-run/restore tested; monetary cost unverified, no applied TTL cleanup acceptance | Engineering; separate explicit review before deletion of existing unique/official evidence |
| Final acceptance suite | All ten requested scenarios on selected profile, real Slack receipts, user disconnect, unauthorized/leakage/duplicate/outage/second-client cases, reports and evidence links | Several local scenarios proven; full deployed suite incomplete | Engineering after gates above; JC opens/authorizes actual clients |

These gates are cumulative. **Configuring Slack alone is insufficient to close #261**, and the remaining technical work is not reassigned to the user. No new models/dashboard work is needed before these gates.

## Schema-drift check delivered in this increment

The profiler now records full index definitions/key columns and views/triggers. `schema_guard` compares schema separately from row counts/file hashes, so ordinary appended data does not falsely count as a schema migration. Changed columns, same-name index column changes, views/triggers and table additions/removals block. Incomplete baseline metadata or missing/duplicate database inventory blocks. The DB opens read-only; no SQL mutation interface is exposed over MCP. Hashes/DDL are evidence of structure, not proof of vintage PIT, freshness or logical integrity.

Operator command on previously verified research copies only:

```bash
PYTHONPATH=src .venv/bin/python -m ai_lab.schema_guard \
  --catalog docs/ai-lab/catalog.json \
  --output /absolute/research/schema-report.json \
  /absolute/research/market_data.sqlite /absolute/research/app.sqlite
```

Exit0 means complete requested inventory, matching baseline schema and SQLite integrity OK; exit1 indicates observed drift/integrity failure. Invalid/incomplete inputs fail before a successful report. CLI paths are operator inputs, never MCP prompt paths. Output may not replace the inputs. Baseline is the immutable source snapshot documented in [audit](audit.md); equality does not make it current production. Do not bless a changed schema by regenerating the baseline without review. This increment runs as default CI tests on generated fixtures without installing MCP extras; it does not add a workflow or download official snapshots during CI.

## Integration and rollback

Only isolated `src/ai_lab`, `tests/ai_lab` and `docs/ai-lab` change. No official databases, Release assets, workflow/schedule/configuration, champions, PAPER/LIVE or cloud resource is changed. Rollback removes the guard/preparation documents; no data migration is performed. The App manifest is inert until an administrator reviews and installs it. No credentials, service account or Slack App was created, and no message was sent.

## Validation of this increment

742 full-repository tests pass with74.14% source coverage; Ruff src/tests/scripts and mypy194 source files pass. Nine new SQLite tests exercise row-only changes, added columns/tables, table removal, changed same-name index key, view/trigger drift, absent/incomplete/duplicate/empty inventories and read-only missing-file behavior. A stdlib-only import with Python `-S` verifies the guard does not require optional MCP/Pydantic extras. Both audited source-copy hashes were checked against the prior catalog before enriching metadata; the real check reports two matching DB schemas and SQLite integrity OK. No official/runtime DB or existing research artifact was deleted or rewritten. Manifest syntax/scope and local documentation links were checked; no manifest validation call or install was made to Slack.

Clean committed-source check and unchanged-copy hashes: [evidence-acceptance.json](evidence-acceptance.json). The evidence follow-up commit changes documentation only.
