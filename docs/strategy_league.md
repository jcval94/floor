# Prospective Strategy League

Strategy League is a **shadow-paper** experiment, isolated from the operational PAPER/LIVE order gateway. Its purpose is to measure prospective strategy performance without rewriting history after prices are known.

## Current epoch

The active contract is:

- league: `strategy_league_v10_d1_w1_champions_10k`;
- initial NAV: **USD 10,000 per member**;
- evidence: new prospective evidence starts only at the first complete v10 EOD genesis;
- v7/v8/v9 evidence remains preserved in separate epochs, but none is stitched into v10;
- automatic promotion: disabled;
- PAPER/LIVE execution: disabled.

Members:

- `weekly_opportunity_ridge`;
- `breakout_protected_by_floor`;
- `mean_reversion_floor_w1`;
- `cross_horizon_asymmetry`;
- `capital_allocation_challenger`;
- `benchmark_spy`;
- `benchmark_equal_weight`.

Cross-Horizon remains a standalone measured member, but it is not allowed to feed the capital allocator while its OOS edge remains weak. This is a quarantine, not a deletion: fresh evidence can justify a future contract change, which would require another explicit freeze/epoch decision.

## Start contract

The league does not start until the same current market session has:

1. complete forecast inputs for the configured universe;
2. the frozen Weekly Opportunity artifact for the current league;
3. current local market bars including SPY.

At genesis every member is cash-only. A decision generated at close `t` can only execute at open `t+1`.

## Weekly Opportunity v10

The v10 Weekly model keeps the v9 cost-adjusted target semantics:

`sign(r_q1) * max(abs(r_q1) - round_trip_cost, 0) / max(q1_downside, 1%)`

The round-trip cost is the exact **61 bps** execution contract. Therefore movements too small to pay friction have target 0. The artifact stores both its target semantics and the cost in bps; EOD fails closed if those values drift from the current execution contract.

The strategy interprets `weekly_opportunity_score × q1_downside` as model-implied **net** directional alpha, so costs are not subtracted twice.

New Weekly entries use the top 10% positive scores. Existing positions can remain while they stay in the top 20% positive scores, providing hysteresis. Review cadence and maximum holding remain 10 sessions.

## Mean Reversion v10

Mean Reversion uses W1 Floor/Ceiling only as payoff/risk anchors. Direction comes from:

`reversal_signal = momentum_10 - momentum_20`

This lets a long reversal be recognized while 20-day momentum is still negative if the shorter 10-day window is improving enough. The symmetrical rule is used for shorts in research.

Current gates are:

- within 3% of the relevant W1 anchor;
- minimum reward/risk 1.20;
- minimum net alpha 25 bps after the 61 bps cost;
- minimum gross-alpha/cost multiple 1.25;
- liquidity, sizing and M3 context still apply.

## Exact execution-cost contract

Research gates and Strategy League execution use the same formula:

- buy: 2 bps broker + 24 bps platform + 3 bps slippage = 29 bps;
- sell: 2 bps broker + 24 bps platform + 3 bps slippage + 3 bps sell fee = 32 bps;
- full round trip: **61 bps**.

The formula is centralized in `contracts.trading.round_trip_cost_bps_from_contract`. There is no separate 58 bps strategy gate.

## Frozen evidence

v9 closed after its first prospective session because the serving D1 and W1 champions were later promoted from `robust_range_v3` to `central_skill_ensemble_v1`. Those promotions changed the frozen model-suite hashes, so v9 was not mutated or continued. v10 starts a clean evidence series with the current D1/W1 champions while preserving the same strategy, cost and contract semantics.

At genesis the league freezes hashes for its league/strategy configuration, Weekly artifact and serving model suite. A semantic or parameter change requires a new `league_id`; the old epoch is retained rather than mutated.

Each league history record participates in an audit hash chain. Walk-forward research also keeps one continuous account across model folds: retraining changes the model epoch, not cash, positions, accumulated transaction costs or trade history.

## Capital Allocation Challenger

The allocator combines eligible source strategies after ranking each source internally. It respects position, gross, heat and sector constraints. Consensus can improve ranking, but does not increase the configured per-position risk budget.

For v10:

- Weekly Opportunity: eligible source;
- Breakout: eligible source;
- Mean Reversion: eligible source;
- Cross-Horizon: **not eligible as allocator source** (source weight 0 and `capital_allocator_enabled: false`).

## Promotion review

There is no automatic promotion. Human review only becomes eligible after the configured prospective minimums, including at least 63 sessions, trade-count requirements, drawdown/Sharpe gates, and positive excess return versus SPY and equal-weight.

Historical walk-forward is supporting model-OOS evidence, not prospective evidence. It must not be treated as permission for PAPER or LIVE execution.

## Persistence and publication

Rolling operational state is persisted outside Git in the versioned runtime-state Release. EOD writes Strategy League state, publishes durable runtime/checkpoint state and then explicitly dispatches Pages. Pages restores pinned Release generations before building the public snapshot.

This preserves the causal sequence:

`EOD compute → durable state → Pages publication`

and prevents the dashboard from racing ahead of the state it claims to display.


## v11 intraday shadow ownership

The frozen `strategy_league_v11_intraday_informed_10k` contract keeps EOD as the
official owner of Strategy League history. Intraday execution remains a
non-promotional shadow layer with LIVE trading disabled.

During each market session:

1. T-1 `pending_targets` are copied from the official EOD base.
2. The first valid `strategy_live` heartbeat applies those targets at today's
   OPEN with the exact Strategy League execution primitives for sizing,
   rebalance suppression, slippage, commission, sell fees, stops and takes.
3. Subsequent ~30-minute heartbeats restore the same shadow portfolio, mark it
   with completed 5m bars, and resolve any point-in-time stop/take exits.
4. `intraday_engine` owns the official decision checkpoints
   `OPEN/+2h/+4h/+6h/CLOSE`; delayed runs drain the oldest missing checkpoint
   first and never consume bars after its immutable `checkpoint_at`.
5. EOD advances the frozen v11 league with the final daily bar and then
   reconciles the official trades against the intraday shadow ledger. Material
   unexplained divergence fails closed. Differences caused specifically by 5m
   path ordering versus the frozen daily-OHLC stop-first rule are retained as
   explicit source-granularity explanations, never silently discarded.

The scheduler watchdog remains recovery-only: it dispatches an existing
workflow only when its normal polling run is stale or absent.

### Deliberate v12 candidate

A future v12 may move official target execution ownership from EOD to the OPEN
heartbeat and persist the intraday position ledger as the authoritative league
state. That change would alter frozen execution semantics and therefore must use
a new `league_id`; v11 is not rewritten in place. A v12 proposal should also
decide whether 5m path ordering replaces the current conservative daily-OHLC
stop/take ambiguity rule.
