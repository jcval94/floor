# Central skill experiments — D1 / W1 vs ATR-only

This folder is deliberately isolated from the production model registry. It tests central floor/ceiling prediction only. It does **not** modify or evaluate conformal/risk geometry as evidence of central skill, and it does **not** use strategy/directional-alpha metrics.

## Question

Can D1 and W1 produce positive, repeatable OOS central skill versus the current ATR-only baseline without materially worsening either boundary and without paying unnecessary model complexity?

The primary metric is:

```text
central_skill_vs_atr = 1 - MAE_spread_model / MAE_spread_ATR
```

Spread is not sufficient by itself. Every candidate is also evaluated on floor MAE, ceiling MAE, normalized errors, fold consistency, ticker breadth, volatility regimes, periods, recent folds, compute cost and a locked-test diagnostic.

## Data provenance

The recorded v1 results use the **exact modelable dataset from the successful production retrain artifact**, not a separately downloaded market reconstruction:

- workflow run: `36192729343`
- artifact: `retrain-execute-36192729343`
- artifact id: `10888488674`
- workflow head: `adf658be448d1788fbd2f3d8341d020261fcf1b3`
- rows: `62,750`
- source split policy: 140 validation sessions + 141 held-out test sessions

The experiment reproduces the current `robust_range_v3` validation score before testing new folds. On the source validation split it reproduces approximately:

- D1: model `0.0101755` vs ATR `0.0098410` → about `-3.40%`
- W1: model `0.0226926` vs ATR `0.0222583` → about `-1.95%`

That parity check is important: the experiment is benchmarking against the actual current model contract, not a proxy implementation.

## Targets

For observation date `t`:

```text
floor_delta   = (close_t - future_min_low) / close_t
ceiling_delta = (future_max_high - close_t) / close_t
```

- D1 uses the next complete market session.
- W1 uses the next five complete market sessions.
- Targets are clipped to the same central-model domain `[0.0001, 0.6]` used by the current trainer.

## Temporal validation

The production test block is never used for model fitting, lambda selection, ensemble-weight selection, or the next-stage recommendation.

Pre-test history is evaluated with five expanding OOS folds, each 126 market sessions. For every fold:

1. validation is strictly later than training;
2. a training row is admitted only if its `target_end_date_<horizon>` is strictly before validation begins;
3. an additional embargo is applied before validation: 1 session for D1 and 5 for W1;
4. ATR and every challenger are scored on exactly the same OOS rows.

Shrinkage and ensemble weights use a nested inner chronological split inside the outer training block. No random CV is used.

The original test split is opened only after each candidate configuration is frozen. Its results are diagnostic and are **not** used to choose the next-stage candidates.

## Candidate families and hypotheses

### 1. `atr_only`

Current baseline. Each side uses the training median of `target_delta / normalized_ATR`.

**Hypothesis:** strong structural null model; every other family must justify deviations from it.

### 2. `adaptive_atr`

ATR multiplier conditioned on `vol_regime × trend_sign`, with empirical-Bayes-style shrinkage back toward the global ATR multiplier.

**Hypothesis:** some of the residual is a regime-dependent scale effect, but the regime table should remain small and strongly regularized.

### 3. `current_model`

Frozen experimental reproduction of `robust_range_v3`: boosted-stump anchor plus 20% challenger; floor challenger is ATR median and ceiling challenger is shallow HGB with absolute-error loss.

**Hypothesis:** control for the currently deployed central architecture.

### 4. `residual_ridge`

```text
prediction = ATR_baseline + Ridge(features -> actual_delta - ATR_baseline)
```

**Hypothesis:** low-variance linear residual structure can improve ATR without relearning range scale from scratch.

### 5. `residual_huber`

Same residual target with Huber regression.

**Hypothesis:** robust residual learning should tolerate extreme future excursions better than squared-error corrections.

The main harness uses `max_iter=400`; `results/huber_400_sensitivity.csv` confirms that the higher-iteration fit reproduces the original estimates while removing convergence warnings seen in the first exploratory run.

### 6. `residual_boosted`

ATR plus shallow HistGradientBoosting residual models with absolute-error loss.

**Hypothesis:** modest nonlinear residual structure exists, but model capacity should stay below the current full-geometry learner.

### 7. `ratio_ridge`

Predicts log `floor_delta / ATR` and log `ceiling_delta / ATR` with Ridge, then rescales by ATR.

**Hypothesis:** ATR-normalized target ratios may be more stationary. The v1 evidence rejects this raw linear formulation strongly.

### 8. `spread_asymmetry`

Predicts two geometrically meaningful quantities:

```text
log(width / ATR)
asymmetry = (ceiling_delta - floor_delta) / width
```

Then reconstructs floor and ceiling.

**Hypothesis:** explicitly modeling width and asymmetry aligns training with the spread metric while retaining separate boundaries.

### 9. `atr_model_shrinkage`

```text
prediction = (1 - lambda) * ATR + lambda * current_model
```

`lambda ∈ {0.0, 0.1, ..., 1.0}` is selected only on nested temporal validation with a 2% boundary-regression guard.

**Hypothesis:** the current ML model contains useful corrections but should be pulled much more aggressively toward ATR.

### 10. `conservative_ensemble`

Small pre-registered convex combinations of ATR, residual Ridge, residual Huber and residual HGB. Weights are chosen only on nested temporal validation.

**Hypothesis:** averaging may improve stability, but it is only worthwhile if the gain justifies the added compute and operational complexity.

## Features

Residual/ratio/geometry candidates use a compact market-only feature set already present in FLOOR: normalized ATR, realized/downside/Parkinson volatility, gap, relative volume, price location, SMA slope, beta, relative strength, momentum, volatility regime score, drawdown, range widths/compression, M3 trend context, multi-week slopes, distance to lows, volatility persistence, recent range amplitude, RSI, Bollinger width, VWAP distance and month seasonality.

No forward-looking feature is added. AI-sheet fields are not required by these candidates.

## Metrics

Each candidate produces:

- MAE floor, ceiling and spread in price units;
- normalized MAE floor, ceiling and spread;
- skill vs ATR-only;
- skill vs global median;
- folds won / percentage won;
- qualified folds won under the 2% boundary guard;
- complete skill distribution by fold;
- skill by ticker;
- skill by volatility regime;
- skill by calendar quarter;
- mean skill over the two most recent pre-test folds;
- locked-test diagnostic;
- fit/predict wall-clock cost.

Central interval coverage is retained only as a diagnostic and is never used as evidence that a candidate has better central predictive skill.

## Uncertainty

W1 labels overlap heavily. IID confidence intervals would be misleading. The experiment first averages spread loss cross-sectionally by date and then performs a moving-block bootstrap over dates:

- D1 block length: 5 sessions;
- W1 block length: 10 sessions;
- 2,000 resamples;
- deterministic seed 102.

This preserves local temporal dependence and avoids pretending that the 50 tickers observed on the same day are 50 independent time observations.

## Reproduce

From the repository root, with the same ABT or a later ABT built under the same contract:

```bash
python -m pip install -e '.[modeling]'
python experiments/central_skill_d1_w1/run_experiments.py \
  --dataset data/training/modelable_dataset.json \
  --output experiments/central_skill_d1_w1/results
```

Row-level losses are intentionally not written by default because they are large. Add `--write-row-losses` only for a local deep dive. Compact fold/ticker/regime/period/test summaries remain sufficient to audit the committed conclusions.

## Results

See [`results/REPORT.md`](results/REPORT.md) for the evidence and the pre-test-only next-stage recommendation. The compact machine-readable entry point is [`results/summary_all.csv`](results/summary_all.csv).

## Production boundary

Nothing in this folder is imported by serving, training governance, champion selection or risk geometry. No production champion is replaced by this experiment.