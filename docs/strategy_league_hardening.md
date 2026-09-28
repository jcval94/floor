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

The autonomous market workflows use layered recovery without changing model, signal, cost, or promotion semantics.

- Critical schedules are re-registered with semantically equivalent split cron entries; polling frequency does not increase.
- `scheduler_watchdog` keeps its own hourly cron and also listens to completed `strategy_live`, `intraday_engine`, `eod`, and `monitoring` runs. If any autonomous path survives, its completion wakes the watchdog so stale siblings can be recovered.
- GitHub run inspection and workflow dispatch retry transient API failures three times. A recovery failure makes the watchdog fail visibly instead of silently claiming health.
- Existing intraday and EOD guards remain the authoritative idempotency boundary, so recovery cannot manufacture duplicate checkpoints.
- The prospective observation report compares the hash-chain sessions actually recorded by the Strategy League with the US market sessions expected between genesis and the latest EOD. Missing sessions are surfaced as `GAP_DETECTED` and are not backfilled or hidden.
- Pages displays the continuity result alongside the prospective evidence.

A total GitHub Actions outage in which no repository event executes cannot be repaired by code inside the same repository. The external silence monitor is therefore the final alarm for that narrow failure mode; repository recovery remains self-contained whenever at least one autonomous workflow event is alive.

