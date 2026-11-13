# OKR #261 — status and path to 2026-12-31

This checkpoint does not close #261 or authorize integration under #253. PRs are stacked drafts: [#262 audit/ADR](https://github.com/jcval94/floor/pull/262) → [#263 core](https://github.com/jcval94/floor/pull/263) → evidence/manuals follow-up. No merge, deployment, official workflow or production-state modification.

| KR | Goal | Evidence achieved | Status | Risks / remaining |
|---|---|---|---|---|
| KR1 MCP | Remote HTTPS, real data, authenticated, two clients | Official SDK 1.30.0, stdio + private HTTP, 21 typed tools; real Python SDK and Inspector query/submit/read; authenticated HTTP tests | Partial | No authorized remote host/TLS/OAuth deployment. No real Codex/Claude assistant session or direct ChatGPT app test |
| KR2 Research | Three reproducible research classes | Model comparison, feature audit, controlled feature-ablation executed on verified snapshot with same purged folds and baseline | Demonstrated locally | Slack E2E absent. Frozen production-champion, VIX and existing-strategy backtest adapters still required; no fabricated substitution |
| KR3 Reproducibility | 100% accepted identity/config/data/state/errors/artifacts; reconstruct two | Atomic acceptance provenance (schema v2); execution refuses changed code/environment. Two real model/audit reconstructions after backup/restore have identical metrics, audit values and OOS predictions | Demonstrated for local harness | Not proof of long-term availability; no transitive lock/image digest yet. Official-state recovery and vintage data are separate |
| KR4 Slack | Ten real E2E deliveries incl. errors/retries | Actual Web API adapter + durable outbox, controlled retry/dedup tests, independent notification state | Blocked on configuration | Zero real deliveries. Needs authorized workspace App, private channel membership/ID and managed token; mock ts never count |
| KR5 Data | Complete critical catalog, integrity/temporal contracts, architecture | Eight physical tables in two real snapshot DBs; read-only catalog DDL/keys/indexes/counts/hashes; NYSE sessions/partial-candle gates, availability/hash/leakage tests | Partial | Full external dataset/resource inventory, logical orphan review and official migration/restore/soak not completed |
| KR6 Autonomy/cost | Disconnect survival, concurrency, budgets, forbidden operations rejected | Separate worker finishes after client disconnect; one-worker lock; daily/monthly/actor caps; zero paid backend; cancellation/timeouts; deny-by-default HTTP/roles | Demonstrated locally | Persistent host/supervisor must be installed by operator. No verified free remote profile/provider bill; process isolation is not an untrusted-code egress sandbox |

## Feasible delivery plan (engineering estimates, not unattended promises)

| Window | Concrete next increment | Gate |
|---|---|---|
| Nov 13–20 | Review stacked prototype; select private host/tunnel and Slack workspace; pin full environment; install isolated supervisor | Explicit hosting/App authorization; no paid resources by default |
| Nov 20–30 | Authorized remote HTTPS/auth staging; ten real Slack deliveries, outages and duplicate requests; Codex/Claude connections | Confirm message ts, secure artifact URLs, identities and negotiated protocol |
| Dec 1–12 | Reviewed frozen-model inference/cutoff adapter; VIX vintage-data feasibility; existing backtest adapter reading 61bps authoritative profile | PIT data and model comparability, no production mutation |
| Dec 12–23 | Full critical resource catalog, logical integrity audit, restore/migration tests, query baseline/monitoring, retention impact dry-run | State-contract #255 and safe cleanup/restore review |
| Dec 24–31 | Rerun all ten end-to-end scenarios and cost/quota soak; KR acceptance record | Measured evidence, no premature OKR closure |

Phase 2 candidates: distributed workers/paid compute, online feature store, persistent remote MLflow service, broad model tuning, arbitrary new-code sandbox, GPU workloads, production promotion. They require evidence of value and explicit budgets/permissions. Novel hypothesis specification and isolated PR review can proceed before runtime support; new untrusted code cannot inherit production credentials.

The Dec 31 target is feasible only if account/hosting/Slack permissions are resolved promptly. This conversation does not schedule future work or promise a Work session will keep running. Only an installed worker system is responsible for autonomous jobs.
