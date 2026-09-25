# Strict challenger validation — D1 / W1

This folder is the promotion-stage validation for the central D1/W1 range models. It is intentionally isolated under `experiments/central_skill_d1_w1/strict_validation/` and does not modify the production model registry, risk geometry, conformal calibration, or strategy alpha.

## Decision

Test whether the best phase-1 candidates still beat **ATR-only** and the **exact current champion** on a final temporal holdout, with no hyperparameter or ensemble-weight tuning on that holdout.

## Frozen candidates

The candidate set is inherited from phase 1:

- `spread_asymmetry`: Ridge models `log(width / ATR)` and asymmetry, then reconstructs floor/ceiling.
- `residual_boosted`: ATR-only anchor plus shallow absolute-error HGB residual learners.
- `atr_model_shrinkage`: convex shrinkage between ATR and the current architecture; lambda is learned on an inner temporal split of development data only.
- `ensemble_top3_equal`: fixed **1/3 + 1/3 + 1/3** average of the three candidates above. No weights are optimized on final test.

An auxiliary 50/50 `spread_asymmetry + shrinkage` ensemble is included only to test whether residual HGB adds marginal stability.

## Final OOS split

The exact ABT comes from production retrain workflow run `36192729343` / artifact `10888488674`.

For each horizon:

1. development data = source `train + validation` rows whose `target_end_date_<horizon>` is **strictly before** final test start;
2. final evaluation = source `test` split only;
3. no final-test target is used for fitting, lambda selection, feature selection, or ensemble weighting.

Final OOS windows:

- D1: 140 sessions / 7,000 rows, `2026-03-06` through `2026-09-24`;
- W1: 136 sessions / 6,800 rows, `2026-03-06` through `2026-09-18`.

The final challenger fit may use labels crossing the old train→validation boundary because those labels are fully matured before the final test cutoff. The only information boundary that matters at this stage is the final test start.

## Exact incumbent

`run_strict_validation.py` contains a pure-Python interpreter for the serialized `robust_range_v3` champion heads. `verify_strict_validation.py` checks that this reproduces the champion-gate validation score:

- D1 absolute difference: ~`4.1e-13`;
- W1 absolute difference: ~`1.2e-13`.

See `results/verification.json`.

## Inference under overlapping labels

Inference is performed at **date level**, not ticker-row level, so same-day cross-sectional observations are not treated as independent.

- moving-block bootstrap: 5,000 resamples;
- block length: 5 sessions D1, 10 sessions W1;
- HAC / Newey-West loss-differential test: lag 5 D1, lag 10 W1;
- temporal stability: non-overlapping 20-session final-test blocks;
- ticker and volatility-regime breadth are reported separately.

The p-value is supporting evidence only. Promotion classification also considers effect size, bootstrap uncertainty, boundary behavior, ticker breadth, regime breadth, temporal recurrence, and performance drawdown.

## Boundary guard

A candidate cannot be treated as cleanly promotable if either normalized floor MAE or normalized ceiling MAE worsens by more than 2% versus ATR-only.

## Ablation

The strict ablation is fixed before evaluation:

1. HGB direct floor/ceiling, **without ATR anchor**;
2. ATR-only anchor;
3. ATR + residual HGB;
4. adaptive/regime ATR + residual HGB;
5. final equal-weight ensemble.

A second ensemble removes residual HGB to measure its marginal contribution to stability.

## Reproduce

From the repository root, after materializing the same retrain artifact:

```bash
python experiments/central_skill_d1_w1/strict_validation/run_strict_validation.py \
  --dataset data/training/modelable_dataset.json \
  --phase1-runner experiments/central_skill_d1_w1/run_experiments.py \
  --d1-champion data/training/models/d1_champion.json \
  --w1-champion data/training/models/w1_champion.json \
  --output experiments/central_skill_d1_w1/strict_validation/results
```

Then verify the incumbent and split contract with `verify_strict_validation.py`.

## Result

See [`results/REPORT.md`](results/REPORT.md). No champion is promoted by this folder.