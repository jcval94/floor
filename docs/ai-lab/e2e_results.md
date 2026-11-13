# Verification checkpoint — 2026-11-13

Evidence details: [evidence.json](evidence.json), [query_benchmarks.json](query_benchmarks.json). Local isolated profile only. Actual Slack deliveries: **0**. Remote public deployments: **0**. No official state changes.

The final evidence uses an immutable FLOOR runtime snapshot, hash `ec1c4dcfe2dd51180d60ec2fc060dd91a1efeda2aef90498f2907b97e724d370`, source run `43303225713`. Imported GOOGL/MSFT/SPY after NYSE/calendar and candle-completion validation. Three Nov-12 partial daily bars were excluded because they were fetched before the close. 2,931 mature labeled rows; 2,199 shared OOS predictions per full model across three purged expanding folds. No train/test row mixing, no tuning on test. Reconstruction is retrospective provider history, not a vintage prospectivity claim.

## Test matrix

| Requested scenario | Observed result | Limit |
|---|---|---|
| 1 Model comparison | Real Python MCP and independent worker produce fixed Dummy/Ridge/ExtraTrees metrics, MLflow and HTML/JSON | Not frozen champions; no live Slack |
| 2 Feature audit | All-fold fixed-Ridge OOS permutation, missingness/constants/correlations, distribution distance and outlier fences, report | No SHAP, causal claims or automatic serving changes; no Slack |
| 3 New experiment | Registered feature ablation runs; novel VIX/LightGBM/champion/backtest intents require reviewed adapters | Arbitrary novel-code execution is deliberately unavailable |
| 4 Controlled failure | Accepted fixture corrupted, child fails; terminal FAILED, structured error, provenance and outbox retained | Notification mocked; no real Slack |
| 5 Disconnect | Python client closes after queue acceptance; independent worker finishes later | Local bounded harness, not installed remote hosting |
| 6 Unauthorized | Read role cannot submit, owner checks, invalid pages/budgets rejected; HTTP401 and hostile Origin rejection | No public OAuth/penetration/soak evidence |
| 7 Leakage | Future feature_available_at fixture is rejected before execution; missing sessions/partial bar test rejects or excludes | Historical source availability assumptions remain explicit |
| 8 Duplicate | Concurrent identical actor/key yields one run/event; different payload same key rejects | Local transactional store only |
| 9 Slack outage | Injected 429 retries and acknowledged fake sender; experiment stays successful | This is a mock transport, not proof of real Slack delivery |
| 10 Two clients | Official Python SDK 1.30.0 and Inspector CLI 2.15.0 independently discover/call/submit/read | Python negotiates 2025-11-25; Inspector exact revision not separately captured. Real Codex/Claude/ChatGPT account connections untested |

42 selected tests passed (17 lab tests + 25 existing regressions); Ruff and mypy passed for the lab package.

Additional checks: OS child timeout and process-group cancellation; research backup checksums/tamper rejection; two restored research volumes; acceptance provenance retained on failure and code/environment mismatch gate; administrator retention dry-run never deletes evidence. Tests exercise trusted registered code; hostile-code sandbox/egress isolation is not established.

## Measured model comparison

Target: `(close - floor_w1) / close`, no percent-sign ambiguity: below numbers are fractions of origin close. Same 2,199 OOS rows for each candidate. Ranking is exploratory; overlapping five-session labels and repeated ticker dates mean row count is not independent sample size.

| Research estimator | MAE | RMSE | Std of fold MAE |
|---|---:|---:|---:|
| Dummy train mean | 0.01756808 | 0.02461989 | 0.00148480 |
| Ridge alpha=1 | 0.01702644 | 0.02427998 | 0.00179346 |
| ExtraTrees fixed 32/depth5 | 0.01692454 | 0.02402803 | 0.00174563 |

ExtraTrees has the lowest observed MAE under this fixed objective; this is not a statistically established winner, evidence of alpha, a trading recommendation or a production promotion. No calibrated coverage/timing/risk-geometry or net strategy returns were computed. CPU fitting and inference durations are recorded separately; control-plane started/finished and result worker wall time include process/tracking overhead. HTML clearly labels its evaluation-only time.

## Reconstructibility

Two separate restored copies reran model comparison and feature audit. Metrics, feature-audit numbers and serialized OOS predictions were identical. Differences allowed and observed: new experiment/run/MLflow IDs, timestamps, artifact volume locations and measured duration. MLflow local artifact URI prefixes are rebased during restore so new runs do not write into the original volume. Official runtime restore/ledger replay is not part of this test.

Exact original/reconstructed IDs, report/result/prediction/provenance SHA-256 values and final implementation commit are in evidence.json. A dirty-tree flag reflects documentation being prepared during the run; source hashes were recorded and compared with the committed implementation. No raw dataset/prediction payloads are committed to Git. HTML/JSON artifacts are local protected files; no persistent public host is invented.

## Data query measurements

Five read-only queries on the real SQLite copies, 30 repetitions each, warm local cache. p95: market history ~0.000028s, forecasts ~0.0530s, reconciliation ~0.0500s, training cycles ~0.000025s, reviews ~0.000031s. SQL, parameters and EXPLAIN are preserved in query_benchmarks.json. These are not cold-IO, concurrent API or production dashboard/Finder/league latency measurements, and do not close the five-critical-consumer KR.

## Agent guidance check

A fresh agent used the model-comparison playbook for a Ridge+LightGBM request. It correctly identified absent LightGBM/runtime tools and absent Slack evidence, refused substitution or fabricated results, and enumerated data/adapter/auth/notification prerequisites. Repository playbooks do not install personal skills or grant execution rights.

Report QA: HTML parsed successfully (12 sections, 3 metric meters), all three relative artifact links resolve. Browser pixel QA was attempted but Chromium could not start because this executor disallows its Unix socket; visual/responsive review remains open. No rendered QA success is claimed.
