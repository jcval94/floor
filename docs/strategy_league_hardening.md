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

- `scheduler_watchdog` keeps its own hourly cron.
- The watchdog also listens to completed `strategy_live`, `intraday_engine`, `eod`, and `monitoring` runs. If any autonomous path survives, its completion wakes the watchdog so stale siblings can be recovered.
- GitHub run inspection and workflow dispatch both retry transient API failures three times. A recovery failure makes the watchdog fail visibly instead of silently claiming health.
- The watchdog dispatches only workflows with no recent or active run; existing intraday/EOD guards remain the authoritative idempotency boundary.
- Critical cron expressions are split into semantically equivalent entries for this merge so GitHub re-registers the schedules without increasing polling frequency.

This removes the previous single dependency on a watchdog cron watching other crons. It still cannot self-heal a total GitHub Actions scheduler outage in which no repository event runs at all; that final failure mode requires an observer outside GitHub. The external silence watch is therefore an alarm, while the repository remains responsible for recovery whenever at least one autonomous workflow event is alive.
