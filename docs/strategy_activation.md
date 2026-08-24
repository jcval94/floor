# Strategy activation protocol

The strategy pack is intentionally split into three execution modes. Registration is not activation.

## Modes

### `backtest`

Research-only. A strategy may generate theoretical orders when `research_enabled: true`. This mode exists so legacy strategies can still be retested and challengers can be evaluated without opening an execution path.

### `paper`

Prospective simulated execution. Two independent switches are required:

1. `activation.paper_execution_enabled: true`
2. `<strategy>.paper_enabled: true`

Paper does **not** require `canonical_serving_enabled`. This is deliberate: a challenger must be able to accumulate prospective evidence before it is promoted to canonical serving.

### `live`

Real-money execution is fail-closed and requires all of the following:

1. `activation.live_execution_enabled: true`
2. `<strategy>.live_enabled: true`
3. `<strategy>.canonical_serving_enabled: true` while `activation.require_canonical_serving: true`

No current strategy satisfies these conditions.

## Current readiness

| Strategy | Research | Paper | Live | Readiness |
| --- | --- | --- | --- | --- |
| `ai_only` | enabled | disabled | disabled | blocked on demonstrated directional signal |
| `model_only` | enabled | disabled | disabled | blocked on demonstrated directional signal |
| `consensus` | enabled | disabled | disabled | blocked on demonstrated directional signal |
| `mean_reversion_floor_w1` | enabled | disabled | disabled | blocked on demonstrated directional signal |
| `breakout_protected_by_floor` | enabled | disabled | disabled | retest required |
| `weekly_opportunity_ridge` | enabled | disabled | disabled | challenger waiting for out-of-sample evidence |

`promotion_eligible` is also false for every strategy. Activation and model promotion are separate decisions.

## Weekly Opportunity serving contract

`weekly_opportunity_ridge` is registered in the strategy pack, but the strategy adapter does not load or promote a model artifact. It consumes a precomputed `weekly_opportunity_score` only when an upstream research/serving step explicitly supplies one.

The cross-sectional rule matches the existing portfolio experiment:

- long-only;
- rank all scored names descending;
- keep only positive scores;
- select at most the top 20% of the scored universe;
- maximum 20% NAV per selected position;
- q1 floor and ceiling are risk context, not fabricated directional-return fields;
- maximum holding horizon is 10 business days.

The training artifact remains `canonical_serving_enabled = false` until promotion criteria are satisfied.

## Promotion sequence

A safe promotion should happen in separate commits or pull requests so each step remains reviewable:

1. **Backtest evidence:** demonstrate positive and stable out-of-time lift, including higher-cost stress tests and benchmark comparisons.
2. **Prospective paper:** enable only the global paper gate and the chosen strategy's `paper_enabled` gate. Freeze the strategy/model version and record decisions before future prices are known.
3. **Promotion review:** after enough prospective evidence, set `promotion_eligible: true` and review the canonical serving contract. This still does not enable live execution.
4. **Canonical serving:** explicitly promote a validated model/strategy version and set `canonical_serving_enabled: true` only after train/serve parity and safety checks pass.
5. **Live canary:** only after a separate decision, enable the global live gate plus the strategy live gate with a deliberately small capital allocation and rollback criteria.

## Non-negotiable safety property

With the repository defaults committed to `main`, calling the strategy runner in `paper` or `live` mode must return zero candidates and zero orders. Tests enforce this fail-closed property.
