# Prospective Strategy League

The Strategy League is a **shadow-paper** experiment. It is intentionally separate from the operational PAPER/LIVE order gateway.

## Purpose

Measure whether a frozen strategy can create prospective excess return without rewriting history after prices are known.

League v1 contains four independent portfolios:

- `weekly_opportunity_ridge`
- `breakout_protected_by_floor`
- `benchmark_spy`
- `benchmark_equal_weight`

Every member starts with exactly **$100,000 virtual USD on the same session**.

## Start contract

The league does not start until all of the following are available on the same current market session:

1. complete current forecast inputs for the configured universe;
2. the frozen Weekly Opportunity challenger artifact;
3. current local market bars including SPY.

At genesis every member still has $100,000 cash. Decisions generated at close `t` can only execute at open `t+1`.

## Frozen evidence

At genesis the league stores SHA-256 hashes for:

- `config/strategy_league.json`;
- `config/strategies.yaml`;
- the frozen Weekly Opportunity artifact.

If any frozen input changes, the existing league refuses to continue. A changed rule/model must use a new `league_id`; it cannot overwrite the old challenger history.

Each daily audit row also contains `prev_hash` and `record_hash`, forming a hash chain over decisions, trades and portfolio state.

## Execution assumptions

The simulator includes:

- commission: 2 bps;
- slippage: 3 bps;
- sell fee: 3 bps;
- integer shares;
- signal at close `t` -> execution at open `t+1`;
- stop/take-profit checks using the following session's OHLC;
- conservative stop-first handling if stop and take profit are both touched in the same daily bar;
- daily close mark-to-market.

Weekly Opportunity is rescored every five completed league sessions to reduce turnover and compute. Breakout is reviewed at each completed EOD and closes by its session timeout.

## Promotion review

There is **no automatic promotion**. A strategy is only marked eligible for human review after at least 63 prospective sessions and only if all configured checks pass, including:

- minimum trade count;
- Sharpe threshold;
- maximum drawdown threshold;
- positive excess return versus SPY;
- positive excess return versus equal-weight;
- positive result under a simple 3x transaction-cost stress estimate.

Eligibility is evidence for review, not permission to trade real money.

## Compute budget

The daily EOD path is deliberately cheap:

- no Yahoo download;
- no historical backtest rebuild;
- no ML training;
- one local SQLite market-data read;
- one local prediction read;
- simple rolling feature calculations;
- one linear Ridge scoring pass;
- four small virtual portfolio updates.

The Weekly Opportunity artifact is trained **once, manually**, with `.github/workflows/strategy_league_bootstrap.yml`. That bootstrap restores the already-retained market SQLite, builds the modelable dataset locally, trains only the weekly Ridge challenger, deletes the temporary dataset and persists only the small frozen model artifact.

## Persistence

Rolling state is stored below `data/metrics/strategy_league/`, which is already included in Floor's external runtime-state release. The EOD workflow also uploads a compact Strategy League audit artifact with 90-day retention.

## Safety boundary

This implementation does not change any of these operational defaults:

```yaml
paper_execution_enabled: false
live_execution_enabled: false
canonical_serving_enabled: false
paper_enabled: false
live_enabled: false
```

The Strategy League never calls the operational broker/order gateway.
