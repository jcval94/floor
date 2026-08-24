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
