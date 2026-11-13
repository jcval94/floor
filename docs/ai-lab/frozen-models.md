# Frozen FLOOR W1 comparison — extension checkpoint

Adds `frozen_model_comparison` to the existing MCP/queue/worker/tracking/report/outbox flow (contract 1.1). No second executor, no production training/registry mutation. See [ADR-002](ADR-002-frozen-models.md).

## Connect and request

1. Operator imports the verified market snapshot with the existing `ai_lab.import_snapshot` CLI plus `--full-features`. This materializes 49 existing feature-builder inputs, joins same-session SPY benchmark prices, checks NYSE continuity and excludes bars fetched before close. Earlier compact datasets are still valid for the three original recipes, but cannot silently satisfy this adapter.
2. `floor_list_models` returns research estimators separately from frozen artifact IDs, versions, family, SHA-256 and timestamp/cutoff status. Models with unknown timestamp or unsupported family are blocked.
3. `floor_plan_experiment` with `recipe="frozen_model_comparison"`, the new dataset ID and two or three `frozen_model_ids`. Natural-language champion/campeón/congelado intent chooses this recipe; unknown LightGBM/VIX/code requests remain proposals.
4. Validate exact plan, cost and limits; submit idempotently as runner. Consult status/results/artifacts/notification independently. No automatic promotion.

Example: “Compara w1_champion, w1_challenger_20260925T214133Z y w1_challenger_20260924T192816Z sobre la misma ventana y explica las limitaciones”. The client must use catalog IDs, not filesystem paths or stored historical metrics.

## Financial interpretation

The common cutoff is the latest parsed frozen artifact version timestamp, **2026-09-26T02:38:28Z** for the default set. The shared mature-label evaluation window is 2026-09-28 through 2026-11-04: 28 observation dates × GOOGL/MSFT/SPY = 84 rows. Dates/forward W1 targets overlap; these are not 84 independent observations.

This is `post_artifact_reconstructed_holdout`, **out_of_sample_verified=false**. Artifact metadata does not establish an exact training-label or model-selection cutoff; provider history is reconstructed/revisable. Historical AI-sheet inputs are absent. Exact serving fallback/imputation behavior is used and explicitly labeled as an assumption, not reconstructed historical AI observations. Native model feature contracts may differ; all candidates consume the same full source materialization and rows.

No models are fitted or calibrated on this holdout. The train-mean baseline uses only labels completed before the common cutoff; ATR×sqrt(5) is a fixed trivial scale benchmark. Existing `build_runtime_features`, `predict_family_delta` and serialized risk add-ons are reused. Metrics include floor/ceiling relative MAE, floor RMSE, central joint coverage, frozen risk joint coverage/width, shared-date temporal-block dispersion, per-symbol errors, inference duration and artifact size. Timing is not compared.

Measured values and accepted run/experiment/MLflow IDs with artifact hashes are recorded in [evidence-frozen.json](evidence-frozen.json). The real MCP request completed in an independent worker after client disconnection from confirmed RUNNING state (3.94s worker wall time), generated MLflow + HTML/JSON, and results were read through MCP after reconnection. No Slack delivery is claimed until a real acknowledgement is recorded. A lower observed error here is not a statistically established winner or production recommendation.

## Added guards

Acceptance provenance now also hashes JSON model and configuration assets. The worker refuses changed assets/code/environment before computation. Cross-run comparisons reject differing candidate sets/windows or code/model/configuration fingerprints. The original 1.0 checkpoint evidence is preserved; these later runs do not rewrite it.

Tests verify exact equality with the existing FLOOR head predictor, deterministic repeated metrics/predictions, mandatory materialization, future availability rejection, path/model-ID rejection, and model/cost-file inclusion in acceptance provenance. The complete repository suite passes: 712 tests, 73.66% source coverage (60% required). Ruff passes across src/tests/scripts; mypy passes across all 191 source files. The optional-field/idempotency upgrade and concurrent submission paths are covered.

## Remaining gates

No remote hosting, public OAuth, real Slack deliveries, actual assistant-account connections, verified training cutoffs or vintage feature data have been added. Existing strategy comparison needs a separate execution/valuation contract; the portfolio engine's close-based sizing must not be naively wired to next-open fills. Nothing in this adapter enables PAPER/LIVE or changes EOD/league/champions.

## Measured shared-window metrics

| Frozen model / baseline | Floor MAE / origin close | Ceiling MAE / origin close | Risk joint coverage | Rows |
|---|---:|---:|---:|---:|
| w1_champion | 0.02115054 | 0.01534901 | 0.928571 | 84 |
| challenger 20260925T214133Z | 0.02161962 | 0.01828477 | 0.928571 | 84 |
| challenger 20260924T192816Z | 0.02162195 | 0.01829459 | 0.654762 | 84 |
| Mean baseline, pre-cutoff labels only | 0.02123285 | 0.01813632 | N/A | 84 |
| Fixed ATR × sqrt(5) baseline | 0.02761603 | 0.02478432 | N/A | 84 |

The champion's floor error is only marginally lower than the pre-cutoff mean baseline. No significance or durable superiority is established; the short reconstructed window and missing exact cutoffs prevent an OOS/alpha conclusion. Risk coverage alone does not imply better geometry; width is preserved in the structured result.

Compatibility: official Python MCP SDK 1.30.0 and Inspector CLI 2.15.0 both read the frozen result. Inspector repeated the same accepted plan/key and received the same run ID; total jobs stayed one. This does not establish real Codex/Claude/ChatGPT account integration or remote deployment.

Contract-upgrade guard: optional fields added after 1.0 must not create a second job or charge for an identical old idempotency key. Plans are compared after typed default expansion; JSON number spelling (0 vs 0.0) is not a distinct budget. Historical stored plans/provenance remain unchanged.
