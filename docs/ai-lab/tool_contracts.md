# MCP tool contracts v1.3

Authoritative machine-readable schemas: [tool_schemas.json](tool_schemas.json). Output envelope: contract_version, ok, data_status, source, data, warnings, error. Validation/auth failures return ok=false and safe error code; SDK schema failures may use standard MCP isError. No free-form shell/SQL tool.

| Tool | Parameters | Minimum role |
|---|---|---|
| floor_get_status | none | read_only |
| floor_get_usage | days (1–90, default 7) | read_only (own actor); administrator sees all actors |
| floor_get_operational_readiness | none | administrator |
| floor_get_capabilities | none | read_only |
| floor_get_data_catalog | limit, offset | read_only |
| floor_get_data_quality | plan | read_only |
| floor_list_models | none | read_only |
| floor_list_strategies | none | read_only |
| floor_list_features | limit, offset | read_only |
| floor_plan_experiment | question, dataset_id, recipe, drop_features | read_only |
| floor_validate_experiment | plan | read_only |
| floor_estimate_experiment_cost | plan | read_only |
| floor_submit_experiment | plan, idempotency_key | experiment_runner |
| floor_list_experiments | limit, offset | read_only |
| floor_get_experiment_status | run_id | read_only |
| floor_get_experiment_results | run_id | read_only |
| floor_cancel_experiment | run_id | experiment_runner |
| floor_list_experiment_artifacts | run_id | read_only |
| floor_get_notification_status | run_id | read_only |
| floor_resend_experiment_report | run_id | experiment_runner |
| floor_test_slack_connection | none | experiment_runner |
| floor_compare_experiments | run_ids | read_only |
| floor_get_run_provenance | run_id | read_only |
| floor_get_retention_plan | none | administrator |

All run reads/cancellation are owner-scoped; administrator can inspect cross-owner evidence. Catalog entries are shared approved research inputs. Paginators cap limit=50, offset=10000. Artifact references are relative to the protected research root, not invented public URLs.

Plan fields: schema_version, question, recipe, dataset_id, models, frozen_model_ids, strategies, features, drop_features, folds, seed, objective, max_seconds, max_memory_mb, max_cost_usd, notification. Five parametrizable registered recipe types; frozen_model_comparison uses frozen_model_ids from the discovered artifact catalog and requires full feature materialization. Its post-artifact window is not verified OOS. Unknown models/novel VIX/backtest/champion intents require reviewed adapters. Planner is deterministic routing, not a general language-to-code compiler. Client reviews exact parameters before submit.

No API compatibility is claimed beyond contract 1.3. Protocol version negotiation and domain contract version are separate. New optional fields can be additive; removing/renaming required inputs requires a new major contract and client harness.
`floor_list_strategies()` is read-only and returns registered IDs, authoritative cost/risk hashes and benchmark scope. `strategy_comparison` uses `strategies`, objective `net_return`, full materialization and fixed existing champion suite. Plan validation includes cost profile, model assets, shared cutoff/window and OOS-unverified status. Outputs use strategy/net-return/drawdown/turnover/trade-count fields, never fictitious MAE placeholders.

Contract 1.3 adds the two operational queries (24 tools total) and optional receipt fields in notification status. `floor_get_usage` aggregates jobs accepted within the selected trailing interval, not all jobs completed during it. Non-admins cannot choose another actor. Timing p95 is nearest-rank over completed samples; active work is omitted from completed runtime. Cost is a local-only estimate with measured monetary cost `null`, never a cloud invoice. Notification counts distinguish API acknowledgements, injected test senders and legacy DELIVERED rows with no receipt. Independent live verification remains `null` until externally reconciled; there is no mock-to-live conversion. The administrator readiness tool is offline, checks volume integrity/storage and explicitly lists unverified deployment gates; it never sends Slack messages or validates credentials.
