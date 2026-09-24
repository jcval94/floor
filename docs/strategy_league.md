# Prospective Strategy League

Strategy League is a **shadow-paper** experiment, isolated from the operational PAPER/LIVE order gateway. Its purpose is to measure prospective strategy performance without rewriting history after prices are known.

## Current epoch

The active contract is:

- league: `strategy_league_v9_net_target_reversal_10k`;
- initial NAV: **USD 10,000 per member**;
- evidence: new prospective evidence starts only at the first complete v9 EOD genesis;
- v8 evidence remains preserved, but it is not stitched into v9;
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

## Weekly Opportunity v9

The v9 Weekly model is trained on a cost-adjusted target:

`sign(r_q1) * max(abs(r_q1) - round_trip_cost, 0) / max(q1_downside, 1%)`

The round-trip cost is the exact **61 bps** execution contract. Therefore movements too small to pay friction have target 0. The artifact stores both its target semantics and the cost in bps; EOD fails closed if those values drift from the current execution contract.

The strategy interprets `weekly_opportunity_score × q1_downside` as model-implied **net** directional alpha, so costs are not subtracted twice.

New Weekly entries use the top 10% positive scores. Existing positions can remain while they stay in the top 20% positive scores, providing hysteresis. Review cadence and maximum holding remain 10 sessions.

## Mean Reversion v9

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

At genesis the league freezes hashes for its league/strategy configuration, Weekly artifact and serving model suite. A semantic or parameter change requires a new `league_id`; the old epoch is retained rather than mutated.

Each league history record participates in an audit hash chain. Walk-forward research also keeps one continuous account across model folds: retraining changes the model epoch, not cash, positions, accumulated transaction costs or trade history.

## Capital Allocation Challenger

The allocator combines eligible source strategies after ranking each source internally. It respects position, gross, heat and sector constraints. Consensus can improve ranking, but does not increase the configured per-position risk budget.

For v9:

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
