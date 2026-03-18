# floor

README de onboarding tecnico para entender el flujo real del sistema, desde la ingesta en Yahoo Finance hasta la visualizacion final en el dashboard estatico.

Este documento esta basado solo en lo que existe hoy en el repositorio: codigo, workflows, tests y artefactos versionados. Cuando una pieza no esta implementada, esta rota o es ambigua, se indica de forma explicita.

## Mapa breve del flujo detectado

```text
GitHub Actions / CLI local
  -> config/universe.yaml + src/floor/universe.py
  -> src/storage/yahoo_ingest.py::ingest_yahoo_to_db
  -> src/storage/market_db.py::daily_bars (SQLite)
  -> src/features/build_training_from_db.py::build_rows_from_db
  -> src/features/feature_builder.py::build_features
  -> src/features/labels.py::build_labels
  -> data/training/modelable_dataset.json
  -> branch A: src/models/run_training.py -> data/training/models/*_champion.json
  -> branch B: src/floor/pipeline/intraday_cycle.py::_latest_feature_rows
  -> src/floor/external/google_sheets.py::fetch_recommendations (opcional)
  -> src/forecasting/run_forecast.py::run_forecast_pipeline
  -> src/floor/storage.py::append_jsonl
  -> data/predictions/*.jsonl + data/signals/*.jsonl + data/orders/*.jsonl
  -> data/persistence/app.sqlite
  -> src/floor/reporting/generate_site_data.py::build_dashboard_snapshot
  -> data/reports/dashboard.json
  -> src/utils/pages_build.py::build_pages_data
  -> site/data/*.json
  -> site/assets/app.js + site/*.html
```

Ramas auxiliares detectadas, pero no conectadas de punta a punta con el dashboard principal:

- `src/strategies/run_strategies.py` consume forecasts y produce ordenes teoricas.
- `src/execution/run_paper_trade.py` simula ejecucion paper.
- `src/reporting/*.py`, `src/storage/export_pages_data.py` y `src/storage/history_writer.py` generan reportes/exportaciones auxiliares.
- `src/notifications/*.py` envia mensajes, pero no aparece orquestado en el flujo principal del dashboard estatico.

## Resumen general del sistema

El repositorio implementa una plataforma local y orientada a GitHub Actions para:

- descargar precios diarios desde Yahoo Finance;
- almacenarlos en SQLite local;
- convertirlos en filas y features modelables;
- entrenar artefactos champion/challenger para horizonte `m3`;
- generar predicciones, senales y ordenes intradia;
- consolidar un snapshot operativo;
- publicar un dashboard estatico en `site/`.

No se encontro un backend HTTP, ni endpoints REST, ni una API interna propia. La visualizacion final es un sitio estatico que lee archivos JSON desde `site/data/`.

## Manejo de artefactos de modelos en GitHub

Los artefactos champion/challenger de `data/training/models/*.json` se versionan directamente en el repositorio (sin Git LFS), para que `ingest` e `intraday` puedan leerlos de forma deterministica sin pasos extra de descarga.

Notas operativas:

1. Si un champion aparece como puntero LFS (`version https://git-lfs.github.com/spec/v1`), el workflow falla en validacion.
2. El flujo de reentrenamiento debe publicar payloads JSON reales en `data/training/models/`.
3. Aunque el JSON sea grande, se mantiene en Git por requisito operativo del proyecto.

## Snapshot actual del repo inspeccionado

Estado observado al inspeccionar el repositorio el `2026-03-13`:

- `data/predictions/` contiene `51` archivos `*.jsonl`.
- `data/signals/` contiene `51` archivos `*.jsonl`.
- `data/orders/` solo contiene `.gitkeep`; no hay ordenes persistidas.
- `data/market/` solo contiene `.gitkeep`; no hay `market_data.sqlite` versionado.
- `data/persistence/` solo contiene `.gitkeep`; no hay `app.sqlite` versionado.
- `data/training/models/` solo contiene `.gitkeep`; no hay champions versionados en el repo actual.
- `data/training/review_summary_latest.json` no existe.
- `data/reports/dashboard.json` existe, pero solo refleja `2` predicciones recientes.
- una regeneracion aislada de `build_dashboard_snapshot` sobre los `JSONL` actuales detecto `51` predicciones recientes, asi que `data/reports/dashboard.json` esta desfasado frente a los artefactos crudos.
- `site/data/metrics.json` tiene `status = "no_public_metrics"`.
- `site/data/strategy.json` tiene `status = "no_strategy_report"`.
- `site/data/drift.json` e `site/data/incidents.json` reflejan un estado degradado del sistema para el `2026-03-12`.
- el historico actual de senales contiene `2334` acciones `HOLD`; no se encontraron `BUY` ni `SELL` en los artefactos versionados.

## Arquitectura del flujo

### 1. Orquestacion

Componentes principales:

- `.github/workflows/db_bootstrap.yml`
- `.github/workflows/ingest.yml`
- `.github/workflows/intraday_engine.yml`
- `.github/workflows/eod.yml`
- `.github/workflows/retrain_assessment.yml`
- `.github/workflows/retrain_execute.yml`
- `.github/workflows/monitoring.yml`
- `.github/workflows/pages.yml`
- `src/utils/workflow_guards.py`
- `src/utils/market_session.py`
- `src/floor/calendar.py`
- `src/floor/main.py`
- `Makefile`

Lo que hace:

- decide si el mercado esta abierto;
- detecta checkpoints operativos (`OPEN`, `OPEN_PLUS_2H`, `OPEN_PLUS_4H`, `OPEN_PLUS_6H`, `CLOSE`);
- dispara ingesta, ciclo intradia, revision de training, build del sitio y despliegue;
- deja markers en `data/snapshots/workflow_runs/*.json`.

Estado actual:

- `PARTIAL`.
- Las utilidades de calendario y gating si estan implementadas y hay pruebas que pasan.
- Los workflows existen y muestran una secuencia razonable.
- Los artefactos de incidentes reportan que el `2026-03-12` faltaron `OPEN`, `OPEN_PLUS_2H` y `OPEN_PLUS_4H`, asi que la cobertura intradia real no fue completa.
- Hay workflows deprecados (`intraday.yml`, `training-review.yml`) que ya no son el camino recomendado.

### 2. Configuracion y universo

Componentes principales:

- `config/universe.yaml`
- `config/pages.yaml`
- `config/retraining.yaml`
- `config/strategies.yaml`
- `src/floor/universe.py::parse_universe_yaml`
- `src/floor/config.py::RuntimeConfig`

Lo que hace:

- define el universo principal de tickers;
- expone variables de entorno para `FLOOR_ROOT_DIR`, `FLOOR_DATA_DIR`, `GOOGLE_SHEETS_RECOMMENDATIONS_CSV_URL` y `LIVE_TRADING_ENABLED`;
- centraliza parametros de paginas, retraining y estrategias.

Estado actual:

- `OK`.
- `config/universe.yaml` contiene 50 tickers liquidos de EEUU.
- `RuntimeConfig` esta en uso directo en `src/floor/main.py` y `src/floor/pipeline/intraday_cycle.py`.

## Flujo cronologico detallado

### Etapa 0. Seleccion del universo

Componentes:

- `config/universe.yaml`
- `src/floor/universe.py::parse_universe_yaml`

Hace:

- lee la lista de tickers configurados;
- acepta tanto `symbols:` en raiz como `universe.symbols` por como esta implementado el parser.

Recibe:

- el archivo YAML del universo.

Produce:

- una lista en memoria de simbolos en mayusculas.

Conecta con la siguiente etapa:

- la lista se usa en la ingesta Yahoo, en el build de training rows, y en la construccion de `site/data/universe.json`.

Status actual:

- `OK`.

### Etapa 1. Consulta inicial de datos en Yahoo

Componentes:

- `src/storage/yahoo_ingest.py::fetch_yahoo_chart`
- `src/storage/yahoo_ingest.py::parse_daily_bars`
- `src/storage/yahoo_ingest.py::ingest_yahoo_to_db`
- `Makefile` target `yahoo-ingest`
- workflow `ingest.yml`, job `ingest_critical`, step `Refresh in-repo market DB from Yahoo`
- workflow `retrain_execute.yml`, mismo paso de refresh

Hace:

- llama al endpoint `https://query1.finance.yahoo.com/v8/finance/chart/{symbol}`;
- pide `range`, `interval` y eventos corporativos;
- parsea timestamps y OHLCV;
- reintenta hasta 3 veces ante errores recuperables;
- duerme entre requests para hacer polling responsable.

Recibe:

- lista de simbolos desde `config/universe.yaml`;
- `benchmark` opcional, por defecto `SPY`;
- `range`, `interval` y `sleep_seconds`.

Produce:

- una lista de `DailyBar` en memoria por simbolo;
- un resumen de ingesta;
- escrituras idempotentes en SQLite a traves de `upsert_daily_bars`.

Conecta con la siguiente etapa:

- persiste barras en `data/market/market_data.sqlite`, que luego alimenta `build_rows_from_db`.

Status actual:

- `PARTIAL`.
- El codigo de ingesta y parseo existe y el parseo esta cubierto por prueba.
- Se verifico en aislamiento que `market_db` puede inicializarse y cargar barras.
- No se ejecuto una llamada real a Yahoo en esta inspeccion.
- El repo actual no versiona `data/market/market_data.sqlite`, asi que no hay evidencia persistida de una DB historica actual en el arbol.

### Etapa 2. Almacenamiento historico de mercado

Componentes:

- `src/storage/market_db.py::init_market_db`
- `src/storage/market_db.py::upsert_daily_bars`
- `src/storage/market_db.py::load_daily_bars`
- tabla SQLite `daily_bars`

Hace:

- crea la tabla `daily_bars`;
- aplica `PRIMARY KEY (symbol, ts_utc)`;
- hace `UPSERT` idempotente;
- guarda `fetched_at_utc` y `raw_payload` opcional.

Recibe:

- objetos `DailyBar`.

Produce:

- base SQLite local con OHLCV diario por simbolo.

Conecta con la siguiente etapa:

- `build_rows_from_db` lee desde aqui para construir training rows y features online.

Status actual:

- `OK` a nivel de codigo y simulacion aislada.
- `NOT POPULATED` en el repo versionado, porque `data/market/market_data.sqlite` no esta presente.

### Etapa 3. Construccion de filas crudas para entrenamiento

Componentes:

- `src/features/build_training_from_db.py::build_rows_from_db`
- `Makefile` target `build-training-from-db`
- workflow `retrain_assessment.yml`, paso `Build current modelable dataset from in-repo DB`
- workflow `retrain_execute.yml`, paso `Build modelable dataset from in-repo DB`

Hace:

- carga simbolos del universo;
- agrega `SPY` como benchmark aunque no este en el universo;
- lee barras desde SQLite;
- construye filas por simbolo con OHLCV, `benchmark_close` y columnas AI vacias.

Recibe:

- `data/market/market_data.sqlite`
- `config/universe.yaml`

Produce:

- una lista de filas crudas;
- archivo `data/training/yahoo_market_rows.jsonl` cuando se usa el CLI.

Conecta con la siguiente etapa:

- `src/features/run_features.py` toma este JSONL y lo convierte en dataset modelable.

Status actual:

- `OK` a nivel de codigo y simulacion aislada.
- La etapa depende de que exista `market_data.sqlite`; hoy el repo no versiona esa DB.

### Etapa 4. Feature engineering y labels

Componentes:

- `src/features/feature_builder.py::build_features`
- `src/features/labels.py::build_labels`
- `src/features/run_features.py::build_modelable_dataset`
- `src/features/feature_registry.py`
- `src/features/model_competition.py`

Hace:

- construye features leakage-safe por simbolo;
- calcula retornos, volatilidad, ATR, RSI, MACD, Bollinger, VWAP, relative strength, slopes, distancias a minimos, features AI y alineacion entre horizontes;
- genera labels futuros para `d1`, `w1`, `q1` y `m3`;
- asigna `split` (`train`, `validation`, `test`);
- crea folds walk-forward;
- documenta el contrato de targets y la missingness del dataset.

Recibe:

- filas crudas con OHLCV, benchmark y opcionales columnas AI.

Produce:

- artefacto `data/training/modelable_dataset.json` con:
  - `rows`
  - `feature_registry`
  - `walk_forward_folds`
  - `missingness_report`
  - `target_documentation`
  - `final_model_columns`
  - `model_competition`
  - `horizon_coverage`

Conecta con la siguiente etapa:

- `src/models/run_training.py` consume este JSON para entrenar champions;
- `src/floor/training/review.py` intenta usarlo para revisar drift;
- el ciclo intradia no usa este archivo directamente: recomputa features desde la DB en linea.

Status actual:

- `OK`.
- Los tests de features, labels, leakage y contratos pasan.
- Esta es una de las capas mejor cubiertas del repo.

### Etapa 5. Entrenamiento y seleccion de champions

Componentes:

- `src/models/run_training.py::run_training`
- `src/models/train_value_models.py::train_floor_m3_value_model`
- `src/models/train_timing_models.py::train_floor_week_m3_timing_model`
- `src/models/select_champion.py::select_and_persist_champion`
- `scripts/retrain_models.sh`
- workflow `retrain_execute.yml`

Hace:

- carga `modelable_dataset.json`;
- separa train/validation;
- entrena un modelo `value` para `floor_m3`;
- entrena un modelo `timing` para `floor_week_m3`;
- persiste challenger y champion en `data/training/models/`;
- guarda metricas en `data/training/metrics/`.

Recibe:

- el artefacto `data/training/modelable_dataset.json`.

Produce:

- `data/training/models/value_champion.json`
- `data/training/models/timing_champion.json`
- challengers timestamped
- metricas de entrenamiento

Conecta con la siguiente etapa:

- `src/forecasting/load_models.py::ChampionModelSet` intenta cargar estos champions en el ciclo intradia.

Status actual:

- `BROKEN` en el camino real por CLI.
- La funcion Python `run_training(..., tasks=None)` si pudo ejecutarse en aislamiento.
- El CLI real `python -m models.run_training --tasks value,timing` falla porque `src/models/tasks.py::normalize_model_tasks` devuelve `None` cuando recibe un string.
- `scripts/retrain_models.sh` usa justamente ese CLI, asi que el workflow `retrain_execute.yml` queda afectado.
- `src/models/dataset_summary.py::summarize_modelable_rows` devuelve `None` en su estado actual; no rompe todos los caminos, pero deja el resumen de dataset incompleto.
- En el repo actual no hay champions versionados dentro de `data/training/models/`.

### Etapa 6. Revision de training y retraining assessment

Componentes:

- `src/floor/training/review.py::run_training_review`
- `src/monitoring/run_retrain_assessment.py`
- `src/monitoring/drift_detection.py`
- workflow `retrain_assessment.yml`

Hace:

- carga el dataset modelable;
- lee champions actuales;
- calcula drift de features, schema, target y performance;
- escribe `data/training/reviews.jsonl`;
- escribe `data/training/review_summary_latest.json`.

Recibe:

- `data/training/modelable_dataset.json`
- `data/training/models/*_champion.json`
- `config/retraining.yaml`

Produce:

- historia de revisiones;
- resumen latest para decidir auto-retrain.

Conecta con la siguiente etapa:

- `retrain_execute.yml` intenta leer `tasks_for_auto_retrain` desde `review_summary_latest.json`.
- `utils/pages_build.py` usa `review_summary_latest.json` y `reviews.jsonl` para construir `site/data/models.json`.

Status actual:

- `BROKEN`.
- `src/floor/training/review.py` no puede importarse porque depende de `src/models/inference.py`, y ese archivo esta truncado.
- `data/training/review_summary_latest.json` no existe en el repo inspeccionado.
- Aunque existe `data/training/reviews.jsonl`, el resumen latest que espera el pipeline no esta presente.

### Etapa 7. Reconstruccion de features online para el ciclo intradia

Componentes:

- `src/floor/pipeline/intraday_cycle.py::_latest_feature_rows`
- `src/features/build_training_from_db.py::build_rows_from_db`
- `src/features/feature_builder.py::build_features`

Hace:

- vuelve a leer la SQLite de mercado;
- filtra solo los simbolos pedidos para el ciclo;
- construye features;
- se queda con la ultima fila disponible por simbolo.

Recibe:

- `data/market/market_data.sqlite`
- lista de simbolos.

Produce:

- `market_rows` listos para forecasting.

Conecta con la siguiente etapa:

- `run_forecast_pipeline` recibe estas filas junto con senales AI externas.

Status actual:

- `IMPLEMENTED`, pero el ciclo completo no arranca hoy porque la importacion de forecasting esta rota.
- Punto importante: el flujo online no reutiliza `data/training/modelable_dataset.json`; recalcula features directamente desde la DB.

### Etapa 8. Integracion opcional de recomendaciones externas

Componentes:

- `src/floor/external/google_sheets.py::fetch_recommendations`
- `src/forecasting/merge_ai_signal.py::merge_market_with_ai_signal`
- variable de entorno `GOOGLE_SHEETS_RECOMMENDATIONS_CSV_URL`

Hace:

- descarga un CSV publico de Google Sheets;
- valida columnas `symbol`, `action`, `confidence`, `note`;
- mezcla esa informacion con la fila de mercado;
- calcula `ai_weight` y `ai_effective_score`.

Recibe:

- URL CSV opcional.

Produce:

- un `ai_by_symbol` en memoria y filas enriquecidas para forecasting.

Conecta con la siguiente etapa:

- `generate_forecasts` usa las columnas AI para sesgar confidence, alignment y scores.

Status actual:

- `PARTIAL`.
- El codigo existe y es opcional.
- No se encontro una URL configurada en el repo, asi que la rama es dependiente del entorno.

### Etapa 9. Forecasting

Componentes:

- `src/forecasting/run_forecast.py::run_forecast_pipeline`
- `src/forecasting/generate_forecasts.py::generate_forecasts`
- `src/forecasting/load_models.py::ChampionModelSet`
- `src/forecasting/rank_opportunities.py::rank_opportunities`
- `src/forecasting/render_time_labels.py::render_horizon_time_labels`
- `src/models/inference.py`

Hace:

- carga champions `value` y `timing`;
- calcula pronosticos `d1`, `w1`, `q1` y `m3`;
- aplica fallback neutral `ai_horizon_alignment=0.0` cuando no hay senal AI completa, sin bloquear `m3` por esa ausencia aislada;
- deriva `confidence_score`, `composite_signal_score`, `reward_risk_ratio`;
- genera listas de oportunidades, bloqueados y dashboard humano.

Recibe:

- `market_rows` enriquecidos con features y AI;
- champions en `data/training/models/`.

Produce:

- `dataset_forecasts`
- `top_opportunities`
- `low_confidence_list`
- `blocked_list`
- `canonical_strategy_output`
- `human_friendly_dashboard`

Conecta con la siguiente etapa:

- `src/floor/pipeline/intraday_cycle.py` recorre `dataset_forecasts` y persiste predicciones/senales/ordenes.

Status actual:

- `BROKEN`.
- `src/models/inference.py` termina en una firma incompleta de `format_champion_version(...)` y provoca `IndentationError`.
- Ese error rompe la importacion de `forecasting.load_models`, `forecasting.generate_forecasts`, `floor.pipeline.intraday_cycle`, `floor.training.review` y `floor.main`.
- El repo si contiene historicos de `data/predictions/*.jsonl`, pero son artefactos previos o generados por una version anterior del flujo.

### Etapa 10. Construccion de senales y ordenes

Componentes:

- `src/floor/pipeline/intraday_cycle.py::_signal_from_prediction`
- `src/floor/pipeline/intraday_cycle.py::maybe_build_order`
- `src/floor/pipeline/intraday_cycle.py::_prediction_payloads`
- `src/floor/schemas.py::{PredictionRecord, SignalRecord, OrderRecord}`

Hace:

- convierte cada fila forecast en tres `PredictionRecord` para `d1`, `w1`, `q1`;
- genera `SignalRecord` desde spread y confidence;
- opcionalmente sobreescribe la accion con la recomendacion externa;
- genera `OrderRecord` solo si la accion no es `HOLD`.

Recibe:

- filas de `dataset_forecasts` del forecasting pipeline.

Produce:

- predicciones, senales y ordenes para persistencia.

Conecta con la siguiente etapa:

- `append_jsonl` persiste cada registro a disco y a SQLite operacional.

Status actual:

- `PARTIAL`.
- La logica existe y `maybe_build_order` esta cubierta por test indirecto.
- Los artefactos versionados muestran `2334` senales `HOLD` y ninguna orden.
- Hay un desacople importante: aunque `generate_forecasts` calcula campos `m3`, `_prediction_payloads` solo persiste `d1`, `w1` y `q1`. La informacion `m3` se pierde antes de llegar al dashboard principal.

### Etapa 11. Persistencia operativa

Componentes:

- `src/floor/storage.py::append_jsonl`
- `src/floor/persistence_db.py::persist_payload`
- `src/floor/persistence_db.py::latest_predictions`
- tabla SQLite `predictions`
- tabla SQLite `signals`
- tabla SQLite `orders`
- tabla SQLite `training_reviews`

Hace:

- escribe cada registro a `JSONL`;
- detecta automaticamente la raiz `data/`;
- espeja el payload en `data/persistence/app.sqlite`.

Recibe:

- `PredictionRecord`, `SignalRecord`, `OrderRecord` o payloads dict.

Produce:

- archivos `data/predictions/*.jsonl`
- archivos `data/signals/*.jsonl`
- archivos `data/orders/*.jsonl`
- base `data/persistence/app.sqlite`

Conecta con la siguiente etapa:

- `build_dashboard_snapshot` intenta leer primero desde SQLite y, si no existe, hace fallback a JSONL.

Status actual:

- `PARTIAL`.
- En aislamiento se verifico que `append_jsonl` crea `data/persistence/app.sqlite` y que `build_dashboard_snapshot` usa SQLite como fuente preferente.
- En el repo actual no hay `data/persistence/app.sqlite` versionada, asi que la persistencia SQLite no esta materializada en los artefactos presentes.

### Etapa 12. Snapshot del dashboard operativo

Componentes:

- `src/floor/reporting/generate_site_data.py::build_dashboard_snapshot`
- `src/floor/main.py` subcomando `build-site`
- workflow `eod.yml`, paso `Build site snapshot`

Hace:

- cuenta archivos de prediccion y senal;
- intenta recuperar la ultima prediccion por `symbol + horizon` desde SQLite;
- si no hay SQLite, toma la ultima fila no vacia de cada archivo `JSONL`;
- escribe `data/reports/dashboard.json`.

Recibe:

- `data/predictions/*.jsonl`
- `data/signals/*.jsonl`
- `data/persistence/app.sqlite` opcional.

Produce:

- `data/reports/dashboard.json`

Conecta con la siguiente etapa:

- `utils.pages_build.py` transforma este snapshot en `site/data/dashboard.json` y `site/data/forecasts.json`.

Status actual:

- `PARTIAL / BROKEN VIA CLI`.
- La funcion `build_dashboard_snapshot` si funciona cuando se llama en aislamiento.
- El comando real `python -m floor.main build-site` falla hoy por el error de importacion en `src/models/inference.py`.
- El archivo versionado `data/reports/dashboard.json` esta desfasado frente a los `JSONL` crudos: hoy contiene 2 predicciones recientes, mientras que una regeneracion aislada detecto 51.

### Etapa 13. Construccion de payloads para el sitio estatico

Componentes:

- `src/utils/pages_build.py::build_pages_data`
- workflow `eod.yml`, paso `python -m utils.pages_build`
- workflow `pages.yml`, jobs `build` y `deploy`

Hace:

- toma `data/reports/dashboard.json`;
- construye `site/data/dashboard.json`, `metrics.json`, `strategy.json`, `universe.json`, `forecasts.json`, `opportunities.json`, `drift.json`, `incidents.json`, `models.json`;
- sanitiza llaves sensibles;
- transforma predicciones en oportunidades de dashboard.

Recibe:

- `data/reports/dashboard.json`
- `data/metrics/public_metrics.json`
- `data/reports/strategy.json`
- `data/reports/retraining_review_2026-03-12.json`
- `data/reports/incident_review_2026-03-12.json`
- `data/training/review_summary_latest.json`
- `data/training/reviews.jsonl`
- `config/universe.yaml`

Produce:

- `site/data/*.json`

Conecta con la siguiente etapa:

- `site/assets/app.js` y las paginas HTML leen estos JSON en el navegador.

Status actual:

- `PARTIAL`.
- La funcion si pudo ejecutarse en esta inspeccion.
- Problemas reales encontrados:
  - depende de un `dashboard.json` que hoy esta stale;
  - usa nombres de archivo fijos para drift e incidentes del `2026-03-12` en vez de descubrir el reporte mas reciente;
  - cuando falta `data/reports/strategy.json`, genera `status = "no_strategy_report"`;
  - cuando falta `data/metrics/public_metrics.json`, genera `status = "no_public_metrics"`.

### Etapa 14. Visualizacion final en el dashboard

Componentes:

- `site/index.html`
- `site/forecasts.html`
- `site/tickers.html`
- `site/strategies.html`
- `site/models.html`
- `site/drift.html`
- `site/incidents.html`
- `site/about.html`
- `site/assets/app.js`
- `site/assets/router.js`
- `site/assets/charts.js`
- `site/assets/utils.js`
- workflow `pages.yml`

Hace:

- renderiza un sitio estatico sin backend;
- consume `site/data/*.json`;
- arma cards, tablas, badges y pequenos graficos SVG.

Recibe:

- `site/data/dashboard.json`
- `site/data/forecasts.json`
- `site/data/opportunities.json`
- `site/data/universe.json`
- `site/data/models.json`
- `site/data/drift.json`
- `site/data/incidents.json`
- `site/data/strategy.json`
- `site/data/metrics.json`

Produce:

- la visualizacion final publicada en GitHub Pages.

Conecta con la siguiente etapa:

- no hay siguiente etapa interna; es el punto final de consumo para usuarios internos.

Status actual:

- `PARTIAL`.
- El frontend estatico existe y el workflow de Pages despliega `site/`.
- La pagina de estrategias hoy cae en fallback porque `site/data/strategy.json` reporta `no_strategy_report`.
- La pagina de modelos hoy cae en fallback porque `site/data/metrics.json` reporta `no_public_metrics`.
- `site/assets/app.js` espera campos `m3` en `forecasts.json`, pero el flujo principal de persistencia no los conserva al escribir `PredictionRecord`. Por eso la narrativa `m3` del frontend no esta conectada completamente al pipeline operativo principal.

## Ramas auxiliares y conexiones internas

### Strategies

Archivos:

- `src/strategies/run_strategies.py`
- `src/strategies/strategy_ai_only.py`
- `src/strategies/strategy_model_only.py`
- `src/strategies/strategy_consensus.py`
- `src/strategies/strategy_mean_reversion.py`
- `src/strategies/strategy_breakout_floor.py`
- `src/strategies/portfolio_allocator.py`
- `config/strategies.yaml`

Hallazgo:

- esta capa consume `forecast_rows` y puede producir ordenes teoricas ricas en contexto;
- no esta invocada por `floor.main run-cycle`;
- tampoco se encontro un writer que lleve su salida a `data/reports/strategy.json`.

Estado:

- `OK` como modulo standalone.
- `NOT WIRED` al dashboard principal.

### Paper execution

Archivos:

- `src/execution/run_paper_trade.py`
- `src/execution/paper_executor.py`
- `src/execution/reconciliation.py`
- `src/execution/portfolio_state.py`

Hallazgo:

- hay una capa completa de paper trading;
- requiere ciclos con senales y market data prearmados;
- no se encontro un workflow principal que conecte esta salida al `site/`.

Estado:

- `OK` como modulo standalone.
- `NOT WIRED` al flujo Yahoo -> dashboard principal.

### Reporting auxiliar

Archivos:

- `src/reporting/daily_report.py`
- `src/reporting/model_report.py`
- `src/reporting/weekly_report.py`
- `src/storage/export_pages_data.py`
- `src/storage/history_writer.py`
- `src/storage/commit_history.py`

Hallazgo:

- existen utilidades para snapshots historicos y reportes;
- aparecen en tests;
- no se detecto que sean el camino principal del dashboard que despliega `site/`.

Estado:

- `IMPLEMENTED`
- `SECUNDARIO`

### Notifications

Archivos:

- `src/notifications/message_builder.py`
- `src/notifications/telegram_notifier.py`
- `src/notifications/ntfy_notifier.py`
- `src/notifications/resend_notifier.py`

Hallazgo:

- la capa de notificaciones existe;
- no se vio integrada en los workflows principales analizados.

Estado:

- `IMPLEMENTED`
- `NO EVIDENCIA DE USO` en el camino Yahoo -> dashboard.

## Estado actual por etapa

| Etapa | Status | Evidencia principal | Impacto |
| --- | --- | --- | --- |
| Orquestacion y gating | PARTIAL | markers y reportes muestran checkpoints faltantes | el dia puede quedar incompleto |
| Ingesta Yahoo | PARTIAL | codigo implementado; no se probo Yahoo en vivo; no hay DB versionada | no hay evidencia persistida de backfill actual |
| Market DB | OK / NOT POPULATED | simulacion local correcta; `data/market/` vacio | la capa existe pero no esta materializada en el repo |
| Training rows | OK | simulacion local correcta | lista para alimentar features |
| Features y labels | OK | tests de features y leakage pasan | capa estable |
| Training CLI real | BROKEN | `models.run_training` falla por `normalize_model_tasks` al recibir string | retraining por CLI/workflow falla |
| Training review | BROKEN | import falla por `src/models/inference.py` truncado | no se genera `review_summary_latest.json` |
| Forecasting | BROKEN | `IndentationError` en `src/models/inference.py` | `run-cycle` no puede arrancar |
| Senales / ordenes | PARTIAL | hay senales historicas, no hay ordenes, todo queda en HOLD | no hay salida operativa accionable en artefactos actuales |
| Persistencia SQLite operacional | PARTIAL | funciona en aislamiento; no hay `app.sqlite` versionada | snapshot hace fallback a JSONL |
| `build_dashboard_snapshot` | PARTIAL / BROKEN VIA CLI | la funcion funciona, `floor.main build-site` falla | `data/reports/dashboard.json` queda stale |
| `utils.pages_build` | PARTIAL | genera `site/data`, pero usa reportes fijos del `2026-03-12` y un dashboard stale | dashboard publicado no refleja todo el historico actual |
| Frontend `site/` | PARTIAL | HTML/JS existen; `strategy.json` y `metrics.json` estan en fallback; `m3` no llega completo | UI visible pero con datos degradados |
| Strategies / execution | OK / NOT WIRED | 18 tests standalone pasaron | no alimentan el dashboard principal |

## Dependencias externas relevantes

Dependencias tecnicas reales encontradas:

- Yahoo Finance chart endpoint: `https://query1.finance.yahoo.com/v8/finance/chart/{symbol}`
- Google Sheets CSV opcional via `GOOGLE_SHEETS_RECOMMENDATIONS_CSV_URL`
- GitHub Actions para orquestacion
- GitHub Pages para despliegue de `site/`
- SQLite local para `market_data.sqlite` y `app.sqlite`
- Python standard library en casi toda la runtime

Dependencias declaradas en `pyproject.toml`:

- runtime: ninguna
- dev: `pytest`, `pytest-cov`, `ruff`

Dependencias operativas auxiliares:

- `bash` para `scripts/retrain_models.sh`
- `make` para los targets del `Makefile`

## Como ubicar rapido cada parte del proceso en el codigo

Si quieres seguir el dato desde el origen hasta la UI, este es el atajo mas rapido:

1. Yahoo y DB de mercado

- `src/storage/yahoo_ingest.py`
- `src/storage/market_db.py`

2. Construccion de dataset

- `src/features/build_training_from_db.py`
- `src/features/feature_builder.py`
- `src/features/labels.py`
- `src/features/run_features.py`

3. Entrenamiento y champions

- `src/models/run_training.py`
- `src/models/train_value_models.py`
- `src/models/train_timing_models.py`
- `src/models/select_champion.py`
- `src/models/tasks.py`
- `src/models/dataset_summary.py`

4. Forecasting online

- `src/floor/pipeline/intraday_cycle.py`
- `src/forecasting/run_forecast.py`
- `src/forecasting/generate_forecasts.py`
- `src/forecasting/load_models.py`
- `src/forecasting/merge_ai_signal.py`
- `src/models/inference.py`

5. Persistencia operativa

- `src/floor/storage.py`
- `src/floor/persistence_db.py`
- `data/predictions/`
- `data/signals/`
- `data/orders/`

6. Snapshot y site data

- `src/floor/reporting/generate_site_data.py`
- `src/utils/pages_build.py`
- `data/reports/dashboard.json`
- `site/data/*.json`

7. Frontend final

- `site/index.html`
- `site/forecasts.html`
- `site/tickers.html`
- `site/strategies.html`
- `site/models.html`
- `site/drift.html`
- `site/incidents.html`
- `site/assets/app.js`

8. Workflows y automatizacion

- `.github/workflows/ingest.yml`
- `.github/workflows/intraday_engine.yml`
- `.github/workflows/eod.yml`
- `.github/workflows/retrain_assessment.yml`
- `.github/workflows/retrain_execute.yml`
- `.github/workflows/pages.yml`
- `src/utils/workflow_guards.py`
- `src/utils/market_session.py`

## Verificacion realizada durante esta documentacion

Verificaciones que si se ejecutaron:

- `pytest -p no:cacheprovider tests/test_execution.py tests/test_strategies.py tests/test_backtest.py tests/test_calendar.py tests/test_session_gating.py tests/test_workflow_guards.py` -> `18 passed`
- `pytest -p no:cacheprovider tests/test_features.py tests/test_no_leakage.py tests/test_configs.py tests/test_calendar.py tests/test_session_gating.py tests/test_workflow_guards.py` -> `17 passed`
- `pytest -p no:cacheprovider tests/test_market_db_pipeline.py::test_parse_daily_bars_filters_missing tests/test_notifications.py::test_message_builder_for_required_events tests/test_notifications.py::test_notifiers_primary_and_secondary_channels` -> `3 passed`
- simulacion aislada de `init_market_db`, `upsert_daily_bars`, `load_daily_bars`, `build_rows_from_db` -> correcta
- simulacion aislada de `append_jsonl` + `build_dashboard_snapshot` -> correcta, con espejo a SQLite
- simulacion aislada de `utils.pages_build.build_pages_data` -> correcta
- regeneracion aislada de dashboard snapshot desde `JSONL` actuales -> detecto 51 predicciones recientes

Verificaciones que fallaron:

- `pytest` completo -> falla en recoleccion por `IndentationError` en `src/models/inference.py`
- `PYTHONPATH=src python -m floor.main build-site` -> falla por el mismo `IndentationError`
- `PYTHONPATH=src python -m models.run_training --dataset ... --output-dir ...` -> falla por `normalize_model_tasks` al recibir string

## Ambiguedades, huecos y piezas no implementadas

Puntos que conviene saber antes de tocar el repo:

- No se encontro codigo que escriba `data/reports/strategy.json`. El frontend si lo espera, pero hoy cae en fallback.
- `monitoring.yml` crea `data/metrics/public_metrics.json` con un payload placeholder y `series: []`; no se encontro una pipeline real de metricas publicas.
- `src/utils/pages_build.py` usa nombres de archivo fijos para drift e incidentes del `2026-03-12`; no hace descubrimiento del reporte mas reciente.
- `src/floor/main.py` llama `build-site`, pero ese subcomando solo construye `data/reports/dashboard.json`. El sitio completo requiere ejecutar ademas `python -m utils.pages_build`.
- `src/floor/pipeline/intraday_cycle.py` genera internamente forecasts con `m3`, pero solo persiste `d1`, `w1` y `q1` en `PredictionRecord`. El dashboard espera datos `m3`, asi que hay una ruptura de contrato entre forecasting y visualizacion.
- `docs/` contiene una copia paralela del sitio y sus datos, pero `pages.yml` despliega `site/`, no `docs/`.
- `scripts/validate_repo.py` compone `PYTHONPATH` con `:` y no con el separador nativo de Windows, asi que el smoke local en este entorno falla antes de ejecutar `floor.main`.

## Orden recomendado para un desarrollador nuevo

Si tu objetivo es entender y depurar el flujo real, el mejor recorrido es este:

1. Lee `src/storage/yahoo_ingest.py` y `src/storage/market_db.py`.
2. Sigue con `src/features/build_training_from_db.py`, `src/features/feature_builder.py` y `src/features/labels.py`.
3. Revisa `src/models/run_training.py`, `src/models/tasks.py`, `src/models/dataset_summary.py` y `src/models/inference.py` para ver los puntos hoy rotos.
4. Pasa a `src/floor/pipeline/intraday_cycle.py` para ver como el flujo online recompone features y persiste resultados.
5. Revisa `src/floor/reporting/generate_site_data.py` y `src/utils/pages_build.py`.
6. Termina en `site/assets/app.js` para entender exactamente que campos consume la UI.
7. Finalmente cruza todo con `.github/workflows/*.yml` para entender que se ejecuta de forma automatizada.

## Conclusiones practicas

El repo tiene una columna vertebral clara y bastante completa:

- Yahoo -> SQLite -> features -> forecasting -> JSONL/SQLite -> snapshot -> `site/data` -> dashboard.

Pero el estado operativo actual no es verde de punta a punta. Los principales bloqueos reales hoy son:

- `src/models/inference.py` truncado;
- training CLI roto por `src/models/tasks.py`;
- `data/reports/dashboard.json` stale frente a los `JSONL` actuales;
- perdida de campos `m3` entre forecasting e interfaz;
- paneles de estrategia y metricas alimentados por placeholders o archivos ausentes.

Si alguien entra nuevo al proyecto, el valor mas alto esta en distinguir entre:

- lo que el repo ya puede hacer como libreria local;
- lo que los workflows intentan automatizar;
- y lo que hoy realmente queda publicado en el dashboard.
