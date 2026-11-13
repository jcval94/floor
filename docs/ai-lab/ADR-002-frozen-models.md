# ADR-002 — Frozen W1 model comparison, contract 1.1

Status: prototype implementation decision, 2026-11-13. Builds on PRs #262 → #263 → #264; no official state/model changes.

Reuse `models.classic_horizon_predictor.build_runtime_features/predict_family_delta` for exact serialized floor/ceiling heads. Never refit models or compare their stored historical metric blobs as if they shared test data. Allowlist model IDs through the local audited JSON registry; preserve file/version/hash, infer only on the same mature labeled observations after the latest parsed artifact version date. Unsupported/missing model contracts fail closed.

A version timestamp is a conservative post-artifact filter, **not verified training-data or selection cutoff**. Where the artifact lacks an exact label-availability training boundary, report `post_artifact_reconstructed_holdout`, `out_of_sample_verified=false`. No prospective, independent statistical winner or production-promotion claim. Baselines fit only on labels completed before the common cutoff. Risk geometry is executed from each frozen artifact, never recalibrated on evaluation outcomes.

Materialize all existing robust-range engineered inputs from verified daily bars with the existing feature builder, a same-session SPY benchmark join, NYSE continuity and close-completion checks. Keep per-input availability metadata. Optional historical AI-sheet inputs are absent and explicitly recorded as unavailable; exact serving default/imputation behavior is a replay assumption, not fabricated historical observations. Models requiring unhandled mandatory inputs are blocked.

Acceptance provenance must hash JSON model and configuration assets as well as Python source. Worker compares these before execution, preventing a queued experiment from silently using changed models/costs. Addition is compatible with existing registered recipes; old evidence remains immutable. No dynamic plug-in code or arbitrary paths are accepted by MCP.

The existing portfolio engine sizes quantities using its `close` field even with OPEN fills. Wiring today's close into next-open sizing would introduce lookahead. Therefore this PR does not add a misleading strategy adapter; that path requires a separate explicit execution/valuation contract and regression proof, without editing official strategy behavior.
