# Registered strategy comparison — research extension

This branch adds a fifth **parametrizable recipe type**, not five hardcoded experiments. It reuses the existing MCP, durable queue, supervised worker, MLflow, reports and Slack outbox. It does not create a second financial engine or change production policies. [ADR-003](ADR-003-strategy-replay.md) defines the contract.

## Request

“Compara mean reversion y breakout bajo las mismas condiciones, incluyendo costos, turnover, drawdown y desempeño contra SPY.”

Discover `floor_list_strategies`, select a registered full-feature dataset, then ask `floor_plan_experiment` with `recipe=strategy_comparison`. The typed plan uses `strategies=[mean_reversion_floor_w1, breakout_protected_by_floor]`, objective `net_return`; validation returns canonical fees, model hashes, cutoff, window and explicit OOS limitations before acceptance. Unknown strategies/code remain proposals requiring a reviewed adapter. No arbitrary configuration paths or SQL/shell are accepted.

The initial source contract supports up to two equities plus SPY and 10–120 signal sessions. Initial equal-weight buy-and-hold is a second benchmark. This is a bounded research demonstration, not full-universe official league parity.

## Execution and financial rules

- Forecasts use existing `generate_forecasts`, frozen D1/W1/Q1/value/timing artifacts, normal M3 abstention and explicit missing historical AI-input assumptions. Target/realization columns are stripped from forecast/strategy input dictionaries.
- Signals are computed after a completed session, with only history through that session. Prior pending targets execute at the next session OPEN using the existing league engine's OPEN sizing. The generic close-sized portfolio backtester is not used.
- Existing gap-aware stop/take, conservative stop-first ambiguity, temporal exits and HOLD-retention functions are reused. New signals use holdings after the current session's fills/exits; the hash chain records an explicit RESEARCH_DECISION event.
- Cost components as well as total round-trip rate must match the authoritative contract. The currently observed round-trip reference is 61bps, including slippage; actual cash fees/fill effects come from executed simulated trades. No caller override of fees or risk thresholds.
- All participants start with the same NAV and first next-open opportunity. All remaining positions liquidate at the last close with the same cost rules, recorded in a separate RESEARCH_LIQUIDATION event. No extra fake market session is appended.
- Per-position sizing and exits are simulated; full broker kill-switch, daily-loss and full-universe sector/heat orchestration are not. No broker connection, PAPER/LIVE order, automatic promotion or official epoch mutation exists.

## Reports and artifacts

Measured net return, drawdown, turnover, trade count, fees, Sharpe when defined, excess vs SPY, NAV curves, forecasts, decision reasons and hash-chained fills are preserved under the run's `backtest_ledger/`. HTML includes a static SVG derived exclusively from measured NAV points. Strategy output never fills model-error columns with invented zeros. Slack formatting supports these strategy metrics, but live delivery remains unconfigured and unverified.

Artifacts are indexed recursively, including partial evidence after failure, cancellation or timeout. Experiment success and Slack delivery remain separate. Costed strategy proposals are registered recipes; novel code is still disabled.

## Evidence interpretation

The first direct replay uses GOOGL/MSFT/SPY over Sep 28–Nov 4, 2026: 28 signal dates, 27 execution sessions. Both strategies produce zero trades under the frozen/configured gates. The report preserves HOLD/rejection reasons; no threshold is relaxed to force attractive activity. Cash outperforming a declining benchmark over a short window is not validated alpha. `ranking_leader.strategy` remains null; descriptive highest-return values are labeled separately.

The window is reconstructed provider history after the newest artifact version timestamp, not a verified training/selection cutoff. `out_of_sample_verified=false`; no prospective, independent-statistical-winner or promotion claim. Unit fixtures force known BUY/HOLD/stop cases solely to verify the reused execution mechanics; those are not market-performance evidence.

Actual MCP/worker execution IDs, metrics and artifacts will be added after executing a clean committed source version. Remote deployment and real Slack delivery are still pending administrator configuration/authorization.
