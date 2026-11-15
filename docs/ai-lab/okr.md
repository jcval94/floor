# OKR #261 — evidence and remaining acceptance

This is a capability assessment, not a completion percentage inferred from file count. The platform is **demonstrated locally and not operational end-to-end**. Remote MCP availability, actual assistant-account connections and real Slack delivery remain blocking. [Issue #261](https://github.com/jcval94/floor/issues/261) remains open. Deadline: 2026-12-31.

## Integrated checkpoints

PRs [#262](https://github.com/jcval94/floor/pull/262), [#263](https://github.com/jcval94/floor/pull/263), [#264](https://github.com/jcval94/floor/pull/264) and [#265](https://github.com/jcval94/floor/pull/265) were merged with explicit user authorization. The next explicit instruction authorized [#266](https://github.com/jcval94/floor/pull/266), merged as `d3fa8f1a32e2fb3cde94bae45bde106dc3c30dbd`; [main CI passed](https://github.com/jcval94/floor/actions/runs/43678719112). These are source integrations, not an MCP deployment or a financial-operation cutover. Official workflows/configuration, Releases, champions, financial state and broker flags are unchanged by this work.

## User research-platform KRs

| KR | Goal | Achieved evidence | Status | Remaining acceptance |
|---|---|---|---|---|
| KR1 MCP | Authenticated remote HTTPS, real data, two assistant clients | Official SDK 1.30.0; stdio/private HTTP; 24 typed tools with operational increment. Python SDK and Inspector actually submit/query; HTTP authorization/Origin tests | Partial, blocking | Authorized persistent host/private tunnel or HTTPS+appropriate OAuth; actual Codex/Claude or equivalent assistant-account connections; ChatGPT only if product permits |
| KR2 Research | Three reproducible investigation classes | Model comparison, feature audit and controlled ablation on verified snapshot with purged shared folds. Frozen W1 heads and mean-reversion/breakout replay added using existing domain code | Demonstrated locally | Slack-linked real acceptance; exact frozen-model training cutoffs and vintage data remain unknown. VIX/new-code execution is still proposal-only |
| KR3 Reproducibility | Accepted identity/code/config/data/state/evidence and two reconstructions | Atomic acceptance provenance, code/model/config/dependency checks, MLflow, recursive artifact hashes. Two model/audit restores reproduced metrics and prediction bytes; receipt-preserving restore tested | Demonstrated in local harness | Full transitive dependency lock/image digest and durable host lifecycle; long-term availability is not proven. Official-state recovery remains separate |
| KR4 Slack | At least ten real E2E runs delivered, including failure/retry | Actual API adapter/outbox, pacing/retry/dedup, separate job/delivery states. Operational increment adds channel-scoped payload-hashed receipts, ambiguous-response gates and injected/legacy labels | Blocked: **0 verified live deliveries / 10** | Authorized workspace/App/private-channel membership/managed secret; actual terminal messages reconciled with channel+ts and run evidence. Mock responses/auth.test do not count |
| KR5 Data | Critical catalog, integrity/temporality and architecture | Eight physical tables from two verified SQLite snapshots cataloged with DDL/keys/indexes/counts/hashes; session/partial-bar/feature-availability gates; five snapshot SQL benchmarks | Partial | Full external-resource inventory, logical-orphan audits, catalog drift CI, official migration/versioning/restore approval and multi-session observation |
| KR6 Autonomy/cost | Disconnect survival, concurrency, budgets and forbidden-action rejection | Worker finishes after client disconnect; lock/concurrency1; daily/monthly/actor/storage caps; timeout/cancel; owner/role controls. Usage/readiness now exposes measured timing/failures/retries and explicit unverified cost | Demonstrated locally | Independent persistent supervisor; verified free remote profile and quota soak; approved retention implementation beyond dry-run. Trusted-process limits are not an untrusted-code/egress sandbox |

Three KRs have local demonstrations; two are partial; real Slack is blocked. **None of the six is signed off as a complete deployed-platform KR.** No aggregate percentage is asserted because the remaining external P0 gates dominate usability.

## Alignment with issue #261's original labels

| Issue criterion | Assessment |
|---|---|
| KR4.0 MCP (blocking) | Local/private prototype proven; no remote endpoint or actual assistant accounts. Official forecast/Finder/history domain query coverage and full response-freshness acceptance remain open |
| KR4.C zero-cost (blocking) | Local-only cloud estimate USD0, no new paid resources. No verified provider invoice/free remote deployment. Storage guard and retention impact dry-run exist; approved TTL deletion/restore case remains open |
| KR4.1 catalog | Eight observed physical tables covered; external resources and automated schema-drift acceptance incomplete |
| KR4.2 identities | Research idempotency/uniqueness proven. Zero-new-duplicates/orphans over ten official sessions has not been demonstrated |
| KR4.3 schema/recovery | Research schema versions1–3, additive receipt migration and isolated restores tested. This does not replace implicit operational migrations or validate official restore/cutover |
| KR4.4 quality/performance | Temporal/checksum/leakage tests and five snapshot-query baselines exist. Full domain query/freshness coverage, realistic ongoing latency soak and all specified operational scenarios remain open |

## Strategy evidence: an honest negative result

Clean implementation `103ee68806b096742d36559b7238ef55f6f33bd3`, run `826cdc1b-f747-473c-a5b7-be824ab3fde7`: Python MCP client disconnected while RUNNING; worker finished SUCCEEDED in6.17s and Inspector read/resubmitted idempotently without a second job. Dataset is immutable/hash-identified. [Recorded evidence](evidence-strategies.json) includes MLflow/artifact hashes and authoritative 61bps cost profile.

GOOGL/MSFT+SPY, Sep28–Nov4: **zero strategy trades**, six benchmark entry/liquidation fills. Cash beating declining benchmarks is not alpha. Replay is post-artifact retrospective history with OOS unverified, not prospective trading or complete broker-risk parity. No filters were weakened to manufacture trades. [Design and limitations](strategy-replay.md).

## Decisions and next gates

1. **Prioritize enablement over more recipes or dashboards.** Preserve local-only/no-paid-fallback architecture, on-demand MLflow+SQLite and existing domain engines. Select a private operator-owned persistent host/tunnel first, subject to actual availability/authorization; do not create public or paid infrastructure implicitly.
2. **Close Slack P0 with evidence.** Administrator configures App/channel/secrets outside Git. Then verify ten distinct terminal E2E executions, safe artifact access, errors/retries and actual messages. A single run's three lifecycle notices are not three E2E executions.
3. **Harden portable operation.** Full dependency lock, install/supervisor runbook, private remote auth/client tests, quota/cost measurement and reviewed retention dry-run/restore. Do not apply deletion to existing unique/official evidence without the required approval.
4. **Complete data acceptance.** External inventory, logical integrity/schema-drift tests, official multi-session observations and migration/cutover under #253/#255 governance. Research success does not certify EOD freshness or the official league.
5. **Novel hypotheses remain a second gate.** VIX requires verifiable historical availability; generated code requires dependency/security review and authorized sandbox. No arbitrary shell/code, production promotion or trading.

Before Dec31, local research/hardening and documentation are feasible within the current scope. Remote availability, live Slack acceptance and assistant-account evidence depend on account/host enablement. Paid scaling, broad tuning/GPU, online feature store and untrusted-code execution are phase2 candidates, justified only by value and explicit limits. This document does not schedule future work; only installed systems can continue after a conversation ends.

Operational follow-up evidence: [operations](operations.md), [machine record](evidence-operations.json). 733 full-repository tests pass with74.06% source coverage; Ruff and mypy193 source files pass. Read-only Python SDK and Inspector checks saw the same persisted successful run; role/owner separation and offline readiness gates were verified. No new job, live Slack delivery or remote deployment was created by these queries.
