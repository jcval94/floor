# Connections, supervision and runbook

Versioned implementation: Python SDK `mcp==1.30.0`, contract `1.0`. Installed SDK advertises protocol revisions 2024-11-05, 2025-03-26, 2025-06-18, 2025-11-25. Streamable HTTP was introduced after 2024; do not assume HTTP support for every legacy revision. Real tests negotiate 2025-11-25; compatibility with the four revisions is not established merely by the SDK's constant. The v2 SDK/protocol upgrade is deliberately deferred until separately tested.

## Persistent worker

Install source in a trusted checkout and dependencies in a venv. Allocate an operator-owned persistent volume, chmod 0700. Use distinct research paths, never operational `data/`. Start `python -m ai_lab.worker --root /absolute/persistent/floor-lab` through systemd or an equivalent host supervisor. Example unit (edit paths/user; not installed by this change):

```ini
[Unit]
Description=FLOOR isolated research worker
After=network-online.target
[Service]
Type=simple
User=floor-research
WorkingDirectory=/opt/floor
Environment=PYTHONPATH=/opt/floor/src
EnvironmentFile=/etc/floor-lab/worker.env
ExecStart=/opt/floor/.venv/bin/python -m ai_lab.worker --root /var/lib/floor-lab
Restart=on-failure
RestartSec=5
KillMode=control-group
NoNewPrivileges=true
PrivateTmp=true
ReadOnlyPaths=/opt/floor
UMask=0077
MemoryMax=5G
CPUQuota=100%
[Install]
WantedBy=multi-user.target
```

Worker environment contains Slack configuration only after administrator authorization. Never run the host worker as root. The module holds an exclusive flock, uses transactional acceptance/claim, and queues FIFO. On worker restart, interrupted RUNNING jobs become FAILED/WORKER_INTERRUPTED and keep existing artifacts. Resume/checkpoint/retry of long training is not implemented; duplicate compute is not silently retried. Cancellation of a RUNNING job is only final after process-group termination is confirmed.

Worker continues while a client exits; host shutdown still stops it. SQLite durability requires the same disk/volume after restart. Do not use an ephemeral Actions runner as the sole queue/outbox database. No background service is installed in ChatGPT Work by these examples.

## Codex local

Official configuration reference checked 2026-11-13: https://learn.chatgpt.com/docs/extend/mcp?surface=cli . Example for the user's own approved configuration, replacing all absolute paths:

```toml
[mcp_servers.floor]
command = "/opt/floor/.venv/bin/python"
args = ["-m", "ai_lab.server", "--root", "/var/lib/floor-lab"]
cwd = "/opt/floor"

[mcp_servers.floor.env]
PYTHONPATH = "/opt/floor/src"
FLOOR_LAB_ACTOR = "jc-codex"
FLOOR_LAB_ROLE = "experiment_runner"
```

Start with `read_only` if execution is not authorized for that assistant. Local role configuration is an operator privilege, not a secret authentication scheme. No Codex profile has been edited and no real Codex model session has been claimed as tested.

## Claude and other stdio clients

Official reference: https://code.claude.com/docs/en/mcp . Standard `mcpServers` object for a trusted local client:

```json
{
  "mcpServers": {
    "floor": {
      "command": "/opt/floor/.venv/bin/python",
      "args": ["-m", "ai_lab.server", "--root", "/var/lib/floor-lab"],
      "env": {
        "PYTHONPATH": "/opt/floor/src",
        "FLOOR_LAB_ACTOR": "jc-claude",
        "FLOOR_LAB_ROLE": "experiment_runner"
      }
    }
  }
}
```

Follow that client's project trust/approval requirements; do not assume JSON layout is universal. Actual tested independent clients are the official Python SDK and MCP Inspector CLI, not Claude Desktop/Code or a real Codex assistant. Inspector smoke uses `@modelcontextprotocol/inspector@2.15.0 --cli --config CONFIG --server floor --method tools/call --tool-name floor_get_capabilities`. Set `MCP_INSPECTOR_SECRET_STORE=memory` for a disposable local test; no account secrets are needed for stdio.

## Private HTTP and future ChatGPT connection

Set `FLOOR_LAB_TOKEN_HASHES` in the MCP supervisor environment to JSON mapping an administrator-managed SHA-256 bearer hash to `{actor,role}`. No token is created or embedded in this repository. Start `python -m ai_lab.server --root /var/lib/floor-lab --transport streamable-http`; the endpoint is **http://127.0.0.1:8765/mcp**, inaccessible remotely by itself. An unauthenticated HTTP request returns 401, not tool discovery. Authenticated invalid Host/Origin is rejected by SDK. Local plaintext is only a loopback testing profile.

An authorized private tunnel/TLS/identity deployment is a separate prerequisite. Do not simply bind to 0.0.0.0 or expose the private bearer profile as a public OAuth service. Inspect negotiated protocol and client auth support; verify actual tool call results, not just ping. A new public endpoint or cloud resource is outside current authorization.

ChatGPT requires an eligible product/workspace integration path and remote/tunnel connectivity approved by the account administrator. Official guidance: https://developers.openai.com/plugins/build/app-quickstart . This work has no endpoint, product permission verification, tunnel, public OAuth or custom ChatGPT app. Therefore **ChatGPT is not connected**. Once those gates are met, add the approved `/mcp` endpoint using the product's current supported app/developer path, authenticate, discover tools and execute a read-only query before granting runner permissions. Do not promise that a local stdio config works directly in ChatGPT or that a generic private bearer token is accepted by every product.

## Incidents and recovery

- Job failed: `floor_get_experiment_status`, safe structured error, inspect preserved artifacts and dataset validation. Do not infer success from MLflow artifacts if the authoritative job state is failed.
- Client disconnected: reconnect as the same server-configured actor; list jobs and inspect ID. Never resubmit without the original idempotency key. Cross-client actors are isolated unless deliberately configured as the same user.
- Slack outage: inspect notification states independently. Restore configuration/retry known failures; reconcile UNKNOWN manually. Never reexecute a successful experiment to resend a message.
- Volume nearing 512MB: new acceptance blocks. Stop worker/client writes; take a consistent SQLite backup using ai_lab.backup CLI (SQLite backup API plus immutable dataset/artifact copies), checksum the entire backup manifest, restore into an empty research directory, and validate selected historical runs. No official-release restore is performed by the lab.
- Schema evolution: new research schema has versioned initialization markers v1/v2 (v2 adds acceptance provenance). Future upgrades need versioned forward-only scripts and a copy/restore rollback. Never downgrade MLflow DB in place or migrate operational SQLite through lab code.
- Worker crash: supervisor kills its process group. Linux parent-death signal also terminates child; orphaned job is marked failed on restart. Review partial artifacts before an explicitly new run. The run is never silently relabeled successful.

Research backup commands (stop MCP/import writes and worker first; destinations must not exist):

```bash
PYTHONPATH=src .venv/bin/python -m ai_lab.backup backup /var/lib/floor-lab /absolute/backup/floor-lab
PYTHONPATH=src .venv/bin/python -m ai_lab.backup restore /absolute/backup/floor-lab /absolute/isolated/restore
```

Restore verifies every manifest hash and rebases only local MLflow artifact URI prefixes. It never points at official state. Queued jobs accepted by older v1 code without acceptance provenance fail closed after upgrade; resubmit explicitly after review. Git source commit and observed environment/source hashes are captured before acceptance, including jobs that later fail or are cancelled. Workers reject changed code/environment rather than silently execute a different experiment.

Install each worker release in an immutable commit-specific checkout; do not edit/switch the live checkout while jobs run. The sample read-only source mount complements provenance gates and prevents the research process from modifying its code/configuration/model assets. Deploy a new source release only after draining or explicitly failing incompatible queued jobs.
