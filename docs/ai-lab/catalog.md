# Observed snapshot schema catalog

Generated read-only from the verified immutable run-43303225713-a1 snapshot; not current production. No row values are exported. SQL defaults, FK and index declarations are observed, not inferred. Logical relationships require business-key review before migration.

## market_data.sqlite

Bytes: 9981952; SHA-256: `7273c735ebfe521dad2b1a11d3a6774218b2beec5e706e8d70bc0ae7df1bbbd9`; integrity: `ok`.

### daily_bars

Observed rows: 51153; declared foreign keys: 0.

| Field | Type | NOT NULL | PK ordinal | Default |
|---|---|---|---|---|
| symbol | TEXT | 1 | 1 | None |
| ts_utc | TEXT | 1 | 2 | None |
| open | REAL | 1 | 0 | None |
| high | REAL | 1 | 0 | None |
| low | REAL | 1 | 0 | None |
| close | REAL | 1 | 0 | None |
| volume | REAL | 1 | 0 | None |
| source | TEXT | 1 | 0 | 'yahoo' |
| fetched_at_utc | TEXT | 1 | 0 | None |
| raw_payload | TEXT | 0 | 0 | None |

```sql
CREATE TABLE daily_bars (
                symbol TEXT NOT NULL,
                ts_utc TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                source TEXT NOT NULL DEFAULT 'yahoo',
                fetched_at_utc TEXT NOT NULL,
                raw_payload TEXT,
                PRIMARY KEY (symbol, ts_utc)
            )
```

Indexes: `[{"seq": 0, "name": "idx_daily_bars_symbol_ts", "unique": 0, "origin": "c", "partial": 0}, {"seq": 1, "name": "sqlite_autoindex_daily_bars_1", "unique": 1, "origin": "pk", "partial": 0}]`

## app.sqlite

Bytes: 855199744; SHA-256: `62a40d7bbb771d5bee84c97339ac9259481e2de9f9d799386505cb442ddf22fa`; integrity: `ok`.

### model_competition_results

Observed rows: 0; declared foreign keys: 0.

| Field | Type | NOT NULL | PK ordinal | Default |
|---|---|---|---|---|
| id | INTEGER | 0 | 1 | None |
| as_of | TEXT | 0 | 0 | None |
| version | TEXT | 0 | 0 | None |
| horizon | TEXT | 0 | 0 | None |
| model_id | TEXT | 0 | 0 | None |
| model_family | TEXT | 0 | 0 | None |
| is_champion | INTEGER | 0 | 0 | None |
| mae_floor | REAL | 0 | 0 | None |
| mae_ceiling | REAL | 0 | 0 | None |
| mae_spread | REAL | 0 | 0 | None |
| test_floor_coverage | REAL | 0 | 0 | None |
| test_ceiling_coverage | REAL | 0 | 0 | None |
| payload_json | TEXT | 1 | 0 | None |

```sql
CREATE TABLE model_competition_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                as_of TEXT,
                version TEXT,
                horizon TEXT,
                model_id TEXT,
                model_family TEXT,
                is_champion INTEGER,
                mae_floor REAL,
                mae_ceiling REAL,
                mae_spread REAL,
                test_floor_coverage REAL,
                test_ceiling_coverage REAL,
                payload_json TEXT NOT NULL
            )
```

Indexes: `[]`

### model_training_cycles

Observed rows: 30; declared foreign keys: 0.

| Field | Type | NOT NULL | PK ordinal | Default |
|---|---|---|---|---|
| id | INTEGER | 0 | 1 | None |
| as_of | TEXT | 1 | 0 | None |
| task | TEXT | 1 | 0 | None |
| training_mode | TEXT | 1 | 0 | None |
| action | TEXT | 1 | 0 | None |
| champion_decision | TEXT | 0 | 0 | None |
| model_name | TEXT | 0 | 0 | None |
| model_version | TEXT | 0 | 0 | None |
| retrained | INTEGER | 1 | 0 | None |
| previous_champion_path | TEXT | 0 | 0 | None |
| previous_champion_version | TEXT | 0 | 0 | None |
| new_champion_path | TEXT | 0 | 0 | None |
| challenger_path | TEXT | 0 | 0 | None |
| metrics_path | TEXT | 0 | 0 | None |
| dataset_path | TEXT | 0 | 0 | None |
| output_dir | TEXT | 0 | 0 | None |
| cv_enabled | INTEGER | 1 | 0 | None |
| cv_folds | INTEGER | 0 | 0 | None |
| hyperparameter_grid_json | TEXT | 0 | 0 | None |
| tuning_summary_json | TEXT | 0 | 0 | None |
| payload_json | TEXT | 1 | 0 | None |

```sql
CREATE TABLE model_training_cycles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                as_of TEXT NOT NULL,
                task TEXT NOT NULL,
                training_mode TEXT NOT NULL,
                action TEXT NOT NULL,
                champion_decision TEXT,
                model_name TEXT,
                model_version TEXT,
                retrained INTEGER NOT NULL,
                previous_champion_path TEXT,
                previous_champion_version TEXT,
                new_champion_path TEXT,
                challenger_path TEXT,
                metrics_path TEXT,
                dataset_path TEXT,
                output_dir TEXT,
                cv_enabled INTEGER NOT NULL,
                cv_folds INTEGER,
                hyperparameter_grid_json TEXT,
                tuning_summary_json TEXT,
                payload_json TEXT NOT NULL
            )
```

Indexes: `[]`

### orders

Observed rows: 0; declared foreign keys: 0.

| Field | Type | NOT NULL | PK ordinal | Default |
|---|---|---|---|---|
| id | INTEGER | 0 | 1 | None |
| symbol | TEXT | 0 | 0 | None |
| as_of | TEXT | 0 | 0 | None |
| action | TEXT | 0 | 0 | None |
| qty | INTEGER | 0 | 0 | None |
| order_type | TEXT | 0 | 0 | None |
| mode | TEXT | 0 | 0 | None |
| payload_json | TEXT | 1 | 0 | None |

```sql
CREATE TABLE orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                as_of TEXT,
                action TEXT,
                qty INTEGER,
                order_type TEXT,
                mode TEXT,
                payload_json TEXT NOT NULL
            )
```

Indexes: `[]`

### prediction_reconciliations

Observed rows: 335934; declared foreign keys: 0.

| Field | Type | NOT NULL | PK ordinal | Default |
|---|---|---|---|---|
| id | INTEGER | 0 | 1 | None |
| prediction_id | INTEGER | 1 | 0 | None |
| prediction_key | TEXT | 0 | 0 | None |
| symbol | TEXT | 1 | 0 | None |
| horizon | TEXT | 1 | 0 | None |
| predicted_as_of | TEXT | 1 | 0 | None |
| resolved_at | TEXT | 1 | 0 | None |
| model_version | TEXT | 0 | 0 | None |
| window_start | TEXT | 0 | 0 | None |
| window_end | TEXT | 0 | 0 | None |
| window_sessions | INTEGER | 0 | 0 | None |
| predicted_floor | REAL | 0 | 0 | None |
| predicted_ceiling | REAL | 0 | 0 | None |
| realized_floor | REAL | 0 | 0 | None |
| realized_ceiling | REAL | 0 | 0 | None |
| abs_error_floor | REAL | 0 | 0 | None |
| abs_error_ceiling | REAL | 0 | 0 | None |
| m3_predicted_week | INTEGER | 0 | 0 | None |
| m3_realized_week | INTEGER | 0 | 0 | None |
| m3_week_hit | INTEGER | 0 | 0 | None |
| payload_json | TEXT | 1 | 0 | None |

```sql
CREATE TABLE prediction_reconciliations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prediction_id INTEGER NOT NULL,
                prediction_key TEXT,
                symbol TEXT NOT NULL,
                horizon TEXT NOT NULL,
                predicted_as_of TEXT NOT NULL,
                resolved_at TEXT NOT NULL,
                model_version TEXT,
                window_start TEXT,
                window_end TEXT,
                window_sessions INTEGER,
                predicted_floor REAL,
                predicted_ceiling REAL,
                realized_floor REAL,
                realized_ceiling REAL,
                abs_error_floor REAL,
                abs_error_ceiling REAL,
                m3_predicted_week INTEGER,
                m3_realized_week INTEGER,
                m3_week_hit INTEGER,
                payload_json TEXT NOT NULL
            )
```

Indexes: `[{"seq": 0, "name": "uq_prediction_reconciliation_id", "unique": 1, "origin": "c", "partial": 0}, {"seq": 1, "name": "uq_prediction_reconciliation_key", "unique": 1, "origin": "c", "partial": 1}]`

### predictions

Observed rows: 120000; declared foreign keys: 0.

| Field | Type | NOT NULL | PK ordinal | Default |
|---|---|---|---|---|
| id | INTEGER | 0 | 1 | None |
| batch_id | TEXT | 1 | 0 | '' |
| symbol | TEXT | 0 | 0 | None |
| as_of | TEXT | 0 | 0 | None |
| event_type | TEXT | 0 | 0 | None |
| horizon | TEXT | 0 | 0 | None |
| floor_value | REAL | 0 | 0 | None |
| ceiling_value | REAL | 0 | 0 | None |
| model_version | TEXT | 0 | 0 | None |
| payload_json | TEXT | 1 | 0 | None |
| prediction_key | TEXT | 0 | 0 | None |

```sql
CREATE TABLE predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id TEXT NOT NULL DEFAULT '',
                symbol TEXT,
                as_of TEXT,
                event_type TEXT,
                horizon TEXT,
                floor_value REAL,
                ceiling_value REAL,
                model_version TEXT,
                payload_json TEXT NOT NULL
            , prediction_key TEXT)
```

Indexes: `[{"seq": 0, "name": "idx_predictions_prediction_key", "unique": 0, "origin": "c", "partial": 0}, {"seq": 1, "name": "uq_predictions_batch_symbol_horizon", "unique": 1, "origin": "c", "partial": 1}]`

### signals

Observed rows: 153456; declared foreign keys: 0.

| Field | Type | NOT NULL | PK ordinal | Default |
|---|---|---|---|---|
| id | INTEGER | 0 | 1 | None |
| batch_id | TEXT | 1 | 0 | '' |
| symbol | TEXT | 0 | 0 | None |
| as_of | TEXT | 0 | 0 | None |
| horizon | TEXT | 0 | 0 | None |
| action | TEXT | 0 | 0 | None |
| confidence | REAL | 0 | 0 | None |
| payload_json | TEXT | 1 | 0 | None |

```sql
CREATE TABLE signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id TEXT NOT NULL DEFAULT '',
                symbol TEXT,
                as_of TEXT,
                horizon TEXT,
                action TEXT,
                confidence REAL,
                payload_json TEXT NOT NULL
            )
```

Indexes: `[{"seq": 0, "name": "uq_signals_batch_symbol_horizon", "unique": 1, "origin": "c", "partial": 1}]`

### training_reviews

Observed rows: 58; declared foreign keys: 0.

| Field | Type | NOT NULL | PK ordinal | Default |
|---|---|---|---|---|
| id | INTEGER | 0 | 1 | None |
| as_of | TEXT | 0 | 0 | None |
| model_name | TEXT | 0 | 0 | None |
| action | TEXT | 0 | 0 | None |
| reason | TEXT | 0 | 0 | None |
| data_drift | REAL | 0 | 0 | None |
| concept_drift | REAL | 0 | 0 | None |
| calibration_drift | REAL | 0 | 0 | None |
| performance_decay | REAL | 0 | 0 | None |
| payload_json | TEXT | 1 | 0 | None |

```sql
CREATE TABLE training_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                as_of TEXT,
                model_name TEXT,
                action TEXT,
                reason TEXT,
                data_drift REAL,
                concept_drift REAL,
                calibration_drift REAL,
                performance_decay REAL,
                payload_json TEXT NOT NULL
            )
```

Indexes: `[]`
