# Strict promotion validation — D1/W1 central skill

This is a promotion-stage validation only. Production champions are unchanged.

## Protocol

- Candidate families were frozen from the previous experimental phase before this run.
- Final evaluation uses only the original held-out `test` split. Candidate models are fit on `train + validation`; no test label is used for fitting, lambda selection, feature selection or ensemble weighting.
- The incumbent is the exact serialized production champion artifact, not a refitted proxy.
- ATR-only is refit on all pre-test data, making the baseline as strong as possible at the final cutoff.
- Equal-weight ensemble is fixed at 1/3 each across Spread+Asymmetry, ATR+Residual HGB and ATR/Incumbent Shrinkage; weights are not optimized on test.
- Uncertainty: 5,000 moving-block bootstrap resamples over date-level losses; HAC/Newey-West on the ATR-minus-candidate daily loss differential.
- Stability windows: non-overlapping 20-session final-test blocks. A final remainder shorter than 10 sessions is omitted.
- Boundary guard: neither floor nor ceiling normalized MAE may regress by more than 2% versus ATR.

## Final OOS comparison

|H|Model|Spread skill vs ATR|Floor skill|Ceiling skill|Skill vs incumbent|Bootstrap 95%|P(skill>0)|HAC p (1-side)|Tickers +|Regimes +|ATR wins periods|Worst 20d skill|Max perf DD / ATR loss|Class|
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
|D1|ATR-only|+0.00%|+0.00%|+0.00%|+7.96%|[+0.00%, +0.00%]|0.0%|1.0000|0%|0%|0%|+0.00%|+0.00%|control|
|D1|Champion actual|-8.64%|-3.41%|-2.49%|+0.00%|[-11.53%, -5.92%]|0.0%|1.0000|26%|0%|100%|-17.59%|+8.87%|control|
|D1|Spread + asymmetry Ridge|+4.32%|-1.86%|+0.15%|+11.93%|[+2.09%, +6.39%]|100.0%|0.0001|90%|100%|0%|-2.05%|+0.48%|merece promoción|
|D1|ATR + residual HGB|+0.65%|-0.09%|+0.62%|+8.56%|[-0.28%, +1.49%]|91.1%|0.0769|74%|100%|43%|-2.95%|+0.66%|prometedor pero necesita más observaciones|
|D1|ATR/current shrinkage|+1.35%|-0.55%|-0.14%|+9.20%|[+0.28%, +2.39%]|99.4%|0.0051|84%|100%|14%|-2.47%|+0.37%|merece promoción|
|D1|Ensemble spread+shrinkage 50/50|+4.99%|-0.89%|+0.41%|+12.55%|[+3.54%, +6.36%]|100.0%|0.0000|98%|100%|0%|-0.48%|+0.20%|merece promoción|
|D1|Ensemble top-3 1/3 cada uno|+4.58%|-0.44%|+0.67%|+12.17%|[+3.47%, +5.69%]|100.0%|0.0000|98%|100%|0%|+1.42%|+0.10%|merece promoción|
|W1|ATR-only|+0.00%|+0.00%|+0.00%|+7.67%|[+0.00%, +0.00%]|0.0%|1.0000|0%|0%|0%|+0.00%|+0.00%|control|
|W1|Champion actual|-8.31%|-4.77%|-2.51%|+0.00%|[-11.62%, -3.71%]|0.0%|1.0000|36%|0%|100%|-15.76%|+8.31%|control|
|W1|Spread + asymmetry Ridge|+3.57%|-1.68%|-0.30%|+10.97%|[+0.16%, +8.20%]|97.8%|0.0573|74%|100%|29%|-3.57%|+1.10%|merece promoción|
|W1|ATR + residual HGB|+0.41%|-0.05%|+0.22%|+8.05%|[-0.84%, +1.57%]|64.3%|0.2568|60%|100%|43%|-2.38%|+0.74%|prometedor pero necesita más observaciones|
|W1|ATR/current shrinkage|+1.43%|-0.68%|+0.03%|+8.99%|[+0.17%, +3.18%]|98.6%|0.0421|66%|100%|14%|-2.36%|+0.42%|merece promoción|
|W1|Ensemble spread+shrinkage 50/50|+4.59%|-0.56%|+0.34%|+11.91%|[+2.55%, +7.62%]|100.0%|0.0008|74%|100%|0%|-0.02%|+0.46%|merece promoción|
|W1|Ensemble top-3 1/3 cada uno|+4.26%|-0.08%|+0.55%|+11.61%|[+2.61%, +6.42%]|100.0%|0.0000|88%|100%|0%|+0.07%|+0.20%|merece promoción|

## Decision

- **Primary challenger: Ensemble top-3 1/3 each — merece promoción as a challenger artifact.** Final OOS spread skill is +4.58% D1 and +4.26% W1 versus ATR-only, with bootstrap intervals fully above zero, 0/7 fixed periods lost to ATR in both horizons, 98%/88% positive tickers, all three volatility regimes positive, and both floor/ceiling within the 2% guard. It also improves spread MAE versus the actual incumbent by +12.17% D1 and +11.61% W1.
- **Best single representation: Spread + asymmetry Ridge — merece promoción.** It retains +4.32% D1 and +3.57% W1 final OOS skill. Its W1 HAC one-sided p-value is 0.057, so the statistical evidence is not uniformly decisive by that single test; however the moving-block bootstrap probability of positive skill is 97.8%, 74% of tickers are positive, all volatility regimes are positive, and only 2/7 fixed W1 periods lose to ATR.
- **Conservative fallback: ATR/current shrinkage — merece promoción as a low-risk challenger.** Skill is smaller (+1.35% D1, +1.43% W1) but bootstrap intervals remain above zero, only 1/7 periods lose to ATR in either horizon, all volatility regimes are positive, and the learned lambda is 0.4 in both horizons using development data only.
- **ATR + residual HGB — prometedor pero necesita más observaciones.** It remains positive (+0.65% D1, +0.41% W1) and does not materially damage either boundary, but both bootstrap intervals cross zero and ATR wins 3/7 fixed periods in both horizons. It should not be promoted as a standalone model yet.

## What the ablation says

1. **The ATR anchor is essential.** Removing it and predicting the complete geometry with the same HGB family produces -4.13% D1 and -3.83% W1 spread skill. The model is not learning a superior absolute geometry from scratch.
2. **Residual learning adds only modest standalone mean skill.** ATR + residual HGB recovers the direct-HGB failure and becomes slightly positive, but the effect is too small to be individually robust on this final block.
3. **The explicit regime layer does not earn its complexity.** Adaptive ATR + residual HGB is worse than plain ATR + residual in D1 and only ~0.2 percentage points better in W1, while its regime breadth is weaker. **Remove the regime layer from the next-stage design.**
4. **Most of the average skill comes from geometry representation + shrinkage.** A 50/50 Spread+Asymmetry / Shrinkage ensemble reaches +4.99% D1 and +4.59% W1, slightly above the three-model ensemble in average skill.
5. **Residual HGB still earns a role as a stabilizer inside the final ensemble.** Adding it reduces average skill slightly, but improves the worst rolling-20-session skill from -0.48% to +1.42% in D1 and from -0.02% to +0.07% in W1, cuts performance drawdown, and raises W1 ticker breadth from 74% to 88%. Given the stated preference for robust repeatability over a marginally higher average, keep it in the ensemble but not as the standalone challenger.

## Incumbent result

The production incumbent is clearly dominated on this final block: -8.64% D1 and -8.31% W1 spread skill versus ATR-only, and ATR wins all 7 fixed periods in both horizons. The strict interpreter reproduces the champion-gate validation score to ~1e-13 (`verification.json`), so this is not an artifact of using a proxy incumbent.

## Ablation — where the skill comes from

|H|Layer|Spread skill vs ATR|Floor skill|Ceiling skill|Bootstrap 95%|ATR wins periods|
|---|---|---:|---:|---:|---:|---:|
|D1|HGB directo sin ATR anchor|-4.13%|-0.69%|+0.55%|[-6.93%, -1.72%]|57%|
|D1|ATR-only|+0.00%|+0.00%|+0.00%|[+0.00%, +0.00%]|0%|
|D1|ATR + residual HGB|+0.65%|-0.09%|+0.62%|[-0.28%, +1.49%]|43%|
|D1|ATR adaptativo + residual HGB|+0.42%|-0.20%|+0.62%|[-0.43%, +1.25%]|43%|
|D1|Ensemble top-3 1/3 cada uno|+4.58%|-0.44%|+0.67%|[+3.47%, +5.69%]|0%|
|W1|HGB directo sin ATR anchor|-3.83%|-0.81%|+0.32%|[-7.47%, -0.57%]|43%|
|W1|ATR-only|+0.00%|+0.00%|+0.00%|[+0.00%, +0.00%]|0%|
|W1|ATR + residual HGB|+0.41%|-0.05%|+0.22%|[-0.84%, +1.57%]|43%|
|W1|ATR adaptativo + residual HGB|+0.61%|-0.21%|+0.23%|[-0.76%, +1.82%]|43%|
|W1|Ensemble top-3 1/3 cada uno|+4.26%|-0.08%|+0.55%|[+2.61%, +6.42%]|0%|

## Cross-horizon classification

- **Spread + asymmetry Ridge: merece promoción.**
- **ATR + residual HGB: prometedor pero necesita más observaciones.**
- **ATR/current shrinkage: merece promoción.**
- **Ensemble top-3 1/3 cada uno: merece promoción.**

## Interpretation

See the companion CSV files for exact ticker, volatility-regime and 20-session-window decompositions. The p-value is supporting evidence only; breadth, temporal recurrence, boundary behavior and drawdown are considered jointly.