# D1/W1 central-skill experiment results

This experiment is isolated from production. No champion, conformal/risk-geometry component, or strategy-alpha component was changed.

## Experimental protocol

- Dataset: exact `modelable_dataset.json` from successful `retrain_execute` run 36192729343 (head `adf658be448d1788fbd2f3d8341d020261fcf1b3`).
- Five expanding OOS folds on pre-test history; 126 sessions per fold.
- Purge: training labels must end strictly before validation starts. Additional embargo: 1 session for D1 and 5 sessions for W1.
- ATR-only is fitted only on each training block and evaluated on exactly the same OOS rows as each candidate.
- The original test split is never used to fit models, choose hyperparameters, shrinkage weights, ensemble weights, or next-stage recommendations. It is reported only as a locked confirmatory diagnostic.
- Uncertainty: 2,000 moving-block bootstrap resamples over date-level mean spread loss (block 5 sessions D1, 10 W1).
- Qualified fold win: positive spread skill vs ATR and neither floor nor ceiling normalized MAE worsens more than 2%.

## Comparative table

|H|Candidate|MAE floor|MAE ceil|MAE spread|Skill ATR|Skill median|Folds|Qualified|Bootstrap 95%|Tickers +|Regimes +|Periods +|Recent 2-fold|Locked test|Seconds|
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
|D1|ATR-only|2.336|2.269|2.310|+0.00%|+16.80%|0/5|0/5|[+0.00%, +0.00%]|0/50|0/3|0/11|+0.00%|+0.00%|0.0|
|D1|Adaptive ATR|2.334|2.268|2.302|+0.37%|+17.10%|5/5|5/5|[+0.19%, +0.56%]|45/50|2/3|9/11|+0.48%|+0.26%|0.1|
|D1|Current robust_range_v3|2.447|2.354|2.447|-6.40%|+11.47%|0/5|0/5|[-9.24%, -3.85%]|8/50|0/3|1/11|-3.59%|-8.50%|2.6|
|D1|ATR + residual Ridge|2.421|2.324|2.243|+2.56%|+18.92%|4/5|0/5|[-0.07%, +5.07%]|41/50|3/3|8/11|+3.87%|+3.00%|1.9|
|D1|ATR + residual Huber|2.333|2.252|2.146|+6.56%|+22.26%|5/5|5/5|[+5.53%, +7.65%]|50/50|3/3|11/11|+6.40%|+0.74%|53.4|
|D1|ATR + residual HGB|2.331|2.250|2.235|+3.02%|+19.31%|5/5|5/5|[+2.48%, +3.71%]|50/50|3/3|11/11|+3.05%|+0.65%|3.7|
|D1|floor/ATR + ceiling/ATR Ridge|2.490|2.472|3.807|-65.56%|-37.75%|0/5|0/5|[-69.80%, -60.87%]|0/50|0/3|0/11|-64.53%|-89.93%|2.0|
|D1|Spread + asymmetry Ridge|2.357|2.269|2.140|+6.89%|+22.53%|5/5|4/5|[+5.05%, +8.67%]|50/50|3/3|10/11|+7.48%|+4.32%|2.1|
|D1|ATR/current shrinkage|2.349|2.271|2.241|+2.78%|+19.11%|5/5|5/5|[+2.01%, +3.47%]|48/50|3/3|11/11|+2.73%|+1.35%|5.0|
|D1|Conservative ensemble|2.341|2.259|2.161|+6.09%|+21.86%|5/5|5/5|[+5.07%, +7.04%]|50/50|3/3|11/11|+6.04%|+4.64%|124.2|
|W1|ATR-only|5.117|5.055|5.389|+0.00%|+15.87%|0/5|0/5|[+0.00%, +0.00%]|0/50|0/3|0/11|+0.00%|+0.00%|0.0|
|W1|Adaptive ATR|5.119|5.054|5.380|+0.13%|+15.98%|5/5|5/5|[+0.02%, +0.26%]|35/50|1/3|9/11|+0.22%|+0.15%|0.1|
|W1|Current robust_range_v3|5.379|5.256|5.582|-3.94%|+12.56%|0/5|0/5|[-7.35%, -0.78%]|19/50|0/3|2/11|-1.72%|-8.09%|2.6|
|W1|ATR + residual Ridge|5.342|5.302|5.210|+3.56%|+18.87%|5/5|0/5|[-0.32%, +6.98%]|36/50|3/3|8/11|+4.05%|+3.07%|1.9|
|W1|ATR + residual Huber|5.162|5.106|4.978|+7.58%|+22.25%|5/5|3/5|[+5.97%, +9.40%]|50/50|3/3|11/11|+7.71%|+0.59%|67.6|
|W1|ATR + residual HGB|5.114|5.053|5.220|+3.06%|+18.44%|5/5|4/5|[+1.84%, +4.55%]|50/50|3/3|11/11|+3.05%|+0.41%|3.7|
|W1|floor/ATR + ceiling/ATR Ridge|5.402|5.427|7.717|-42.29%|-19.71%|0/5|0/5|[-45.66%, -38.37%]|0/50|0/3|0/11|-45.12%|-63.62%|1.9|
|W1|Spread + asymmetry Ridge|5.179|5.123|4.983|+7.57%|+22.24%|5/5|4/5|[+5.41%, +9.67%]|50/50|3/3|10/11|+7.96%|+3.57%|2.0|
|W1|ATR/current shrinkage|5.137|5.068|5.183|+3.74%|+19.02%|5/5|5/5|[+2.76%, +4.76%]|48/50|3/3|11/11|+4.12%|+1.43%|5.2|
|W1|Conservative ensemble|5.110|5.071|5.060|+5.86%|+20.80%|4/5|4/5|[+4.53%, +7.31%]|50/50|3/3|9/11|+7.37%|+4.23%|134.2|

## Evidence by family

- **Current robust_range_v3:** rejected as a central-skill direction in this battery. It lost to ATR in all 5 folds for both horizons; pooled skill was about -6.4% D1 and -3.9% W1. The frozen experimental implementation reproduces the repository validation scores to numerical precision before the new folds are used.
- **Direct floor/ATR + ceiling/ATR Ridge:** rejected quickly. It was strongly negative in every fold and on the locked test. The ratio target in this raw form is not sufficiently stationary/linear.
- **Adaptive ATR:** statistically consistent but tiny: +0.37% D1 and +0.13% W1 pooled. It is useful as a stronger future baseline, not as the main challenger.
- **Residual Ridge:** positive spread skill, but it breached the 2% floor/ceiling guard often enough that it is not a clean central replacement.
- **Residual Huber:** very strong pre-test spread skill (+6.6% D1, +7.6% W1), but W1 failed the boundary guard in 2/5 folds and the locked-test gain compressed to below 1%. A max-400-iteration sensitivity reproduced the same estimates and removed convergence warnings, so the issue is temporal transfer, not optimizer failure.
- **Residual HGB:** positive in every fold, with 5/5 qualified D1 and 4/5 qualified W1. It is materially simpler and cheaper than the conservative ensemble.
- **Spread + asymmetry:** strongest simple representation. It achieved +6.9% D1 and +7.6% W1 pooled pre-test skill, 4/5 qualified folds in each horizon, positive skill for all 50 tickers and all 3 volatility regimes pre-test, and remained +4.3% D1 / +3.6% W1 on the locked diagnostic.
- **ATR/current shrinkage:** exceptionally stable even though its gain is smaller. Lambda was learned only on inner temporal validation (D1 0.3 in every outer fold; W1 0.2–0.4). It won 5/5 qualified folds for both horizons and remained positive in all 11 reported pre-test quarters.
- **Conservative ensemble:** high and broad skill, but computational cost is disproportionate in the current implementation because Huber/base learners are refit repeatedly. It remains a useful upper-bound/control and a candidate for later caching/pruning rather than immediate promotion work.

## Next-stage candidates (selected without test)

1. **Spread + asymmetry Ridge** — D1 +6.89%, W1 +7.57%; qualified folds 4/5 and 4/5; recent two-fold mean +7.48% / +7.96%.
2. **ATR/current shrinkage** — D1 +2.78%, W1 +3.74%; qualified folds 5/5 and 5/5; recent two-fold mean +2.73% / +4.12%.
3. **ATR + residual HGB** — D1 +3.02%, W1 +3.06%; qualified folds 5/5 and 4/5; recent two-fold mean +3.05% / +3.05%.

The locked test is deliberately not part of that selection rule. As a confirmation only, all three selected candidates stayed positive there.

## Huber convergence sensitivity

`huber_400_sensitivity.csv` reruns residual Huber with `max_iter=400` and stricter tolerance. The fold/test skills match the original run to displayed precision and no convergence warning remains. The main harness therefore uses the 400-iteration setting going forward.

## Files

- `summary_all.csv`: compact cross-horizon comparison.
- `fold_results_all.csv`: complete fold distribution and boundary guards.
- `ticker_results_all.csv`, `regime_results_all.csv`, `period_results_all.csv`: pre-test OOS decomposition.
- `test_results_all.csv` plus `test_*_results_all.csv`: locked-test diagnostics only.
- `bootstrap.json`: block-bootstrap uncertainty.
- `metadata.json`: data provenance and protocol.
- Raw row-level loss files are generated only with `--write-row-losses`; they are intentionally not committed.