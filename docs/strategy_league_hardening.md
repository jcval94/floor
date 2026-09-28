# Strategy League hardening invariants

This hardening pass preserves the prospective Strategy League experiment without enabling operational PAPER or LIVE execution.

## Weekly allocation

For `n` investable Weekly Opportunity selections, each target weight is:

`min(1 / n, max_weight_pct_nav)`

With the current 20% cap this guarantees gross target exposure is never above 100% and removes symbol-order/cash-exhaustion bias from the shadow portfolio.

## Weekly holding period

Each newly opened position records both `entry_session` and `entry_session_number`. The 10-session holding rule is counted from Strategy League market sessions, not calendar days. Stop and take-profit checks remain conservative and take precedence; otherwise the position is force-closed at the close of its tenth held market session.

The holding limit is read from `config/strategies.yaml` (`temporal_exit_business_days`) and injected into the runtime League config. Because `strategies.yaml` is part of the frozen League hash contract, changing this rule requires a new League generation rather than silently rewriting an existing experiment.

## Retention

`data/metrics/strategy_league/**` is excluded from generic snapshot pruning. This protects the frozen Weekly challenger, the hash-chained history, current state, leaderboard/status data, and other experiment evidence for the lifetime of the League. The broader runtime-state archive size cap and integrity checks still apply.


## Unattended scheduler resilience

The Strategy League keeps its normal four intraday observations per hour. The `:20` UTC-minute poll is isolated as a dedicated schedule entry and, after completing or failing its own observation work, attempts to dispatch `scheduler_watchdog.yml`.

This creates two independent workflow-level paths inside GitHub Actions:

- the watchdog's own hourly cron;
- an hourly cross-wake from `strategy_live`.

The watchdog still dispatches only workflows with no recent or active run, so the cross-wake cannot create duplicate market checkpoints. Intraday and EOD guards remain authoritative and idempotent.

The cross-wake is deliberately non-fatal for the Strategy League observation. It retries three times and emits a warning if GitHub refuses the dispatch, but it never invalidates already-produced strategy evidence merely because the recovery helper failed. A separate external silence monitor is expected to detect the rarer case where GitHub's scheduler stops creating runs altogether.
