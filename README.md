# FLOOR

FLOOR es un sistema reproducible de **forecasting probabilístico de pisos y techos para acciones**, con operación automatizada principalmente mediante GitHub Actions. Combina datos de Yahoo Finance, SQLite operacional, modelos champion versionados, estado durable en GitHub Releases, backtesting/replay, una Strategy League prospectiva y un dashboard estático auditado en GitHub Pages.

El objetivo del repo no es convertir un rango pronosticado en una recomendación de inversión. FLOOR separa explícitamente:

- **geometría central**: estimación típica de piso/techo;
- **geometría de riesgo**: rango calibrado para contener conjuntamente los extremos realizados;
- **estrategias**: reglas de investigación que consumen forecasts bajo contratos de costos y riesgo;
- **ejecución**: capa separada y actualmente deshabilitada para PAPER/LIVE en la configuración productiva.

## Estado operativo actual

| Componente | Estado |
| --- | --- |
| Forecast D1 / W1 / Q1 | Activo con champions gobernados |
| Floor + timing 3M | Activo mediante champions de value/timing |
| Calibración de riesgo | Joint conformal con tuning temporal |
| Strategy League | **shadow-paper**, epoch v9 |
| PAPER operacional | Deshabilitado |
| LIVE trading | Deshabilitado |
| Promoción automática de estrategias | Deshabilitada |
| GitHub Pages | Build auditado desde `site/` |
| Runtime durable | GitHub Releases, no Git |
| SQLite | Cache/índice operacional, reconstruible |

## Arquitectura actual

```text
Yahoo Finance
  -> market data / SQLite operacional
  -> features + labels
  -> modelos y forecasts
       D1 / W1 / Q1
         -> geometría central
         -> geometría de riesgo joint-conformal
       3M
         -> floor/value
         -> timing
  -> ledgers de predictions/signals + SQLite cache
  -> runtime-state-v1
  -> EOD reconciliation
  -> Strategy League v9 (prospective shadow-paper)
  -> strategy-live-base-v1 / strategy-live-v1
  -> audited Pages build
       + runtime-state-v1
       + research-state-v1
       + monitoring-state-v1
       + strategy-live-v1
       + champions de main
  -> site/
  -> GitHub Pages
```

Los payloads generados bajo `data/` y `site/data/` no son la fuente de verdad versionada en Git. La excepción deliberada es el registro pequeño de artefactos JSON bajo `data/training/models/`.

## Forecasts y confianza

### Horizontes

FLOOR sirve cuatro horizontes principales:

- `d1`: 1 sesión;
- `w1`: 5 sesiones;
- `q1`: 10 sesiones;
- `m3`: floor de 3 meses + modelo de timing.

Los champions de producción son:

- `d1_champion.json`;
- `w1_champion.json`;
- `q1_champion.json`;
- `value_champion.json`;
- `timing_champion.json`.

El directorio `data/training/models/` también puede contener challengers, resultados de competencia y snapshots archivados. **Solo `retrain_execute.yml` puede promover el registro servido de producción.**

### Geometría central vs geometría de riesgo

D1/W1/Q1 separan la predicción central de la incertidumbre. El predictor central no se ensancha artificialmente para mejorar cobertura.

La geometría de riesgo actual usa:

```text
method = joint_validation_conformal_max_residual
target_joint_coverage = 0.80
calibration_policy = nested-temporal-v1
```

La calibración toma el mayor error conjunto entre piso y techo y selecciona, dentro del bloque de calibración, el nivel nominal más pequeño que alcanza el objetivo conjunto en una submuestra temporal posterior. La evaluación de riesgo externa no se utiliza para seleccionar ese nivel.

Esto permite mejorar confianza/cobertura **sin sustituir el predictor central** cuando el challenger no domina su error.

### Skill vs dummy

El pipeline de competencia puede comparar el MAE del spread central contra baselines ingenuos, actualmente incluyendo `global_median` y `atr_only`.

Cuando el diagnóstico está disponible:

```text
skill_vs_dummy = 1 - MAE_modelo / MAE_mejor_dummy
```

Un valor positivo significa reducción de error frente al mejor dummy disponible; no significa rentabilidad esperada.

El gate de champions puede refrescar estos diagnósticos mediante una migración de contrato que conserva los parámetros centrales del incumbent.

El benchmark y auditoría de `robust_range_v3` se documentan en [docs/robust_range_v3.md](docs/robust_range_v3.md).

## Costos de transacción

La autoridad de costos es `config/costs.yaml` y el código común vive en `src/contracts/trading.py`. No se deben hardcodear costos independientes dentro de una estrategia o backtester.

### Strategy research / Strategy League

El contrato actual es:

```text
BUY  = 2 bps broker + 24 bps platform + 3 bps slippage = 29 bps
SELL = 2 bps broker + 24 bps platform + 3 bps slippage + 3 bps sell fee = 32 bps

ROUND TRIP = 61 bps
```

El helper `round_trip_cost_bps_from_contract` centraliza esta fórmula para evitar divergencias entre gates, research y evaluación.

### PAPER

El perfil PAPER es explícitamente distinto en `config/costs.yaml`: actualmente usa `paper_sell_fee_bps: 0`, por lo que su round-trip configurado es 58 bps. Esto no debe reutilizarse como gate de Strategy League ni como costo de research.

## Strategy League

La Strategy League es un experimento **prospectivo shadow-paper** aislado del gateway PAPER/LIVE.

Contrato actual:

- league: `strategy_league_v9_net_target_reversal_10k`;
- NAV inicial: USD 10,000 por miembro;
- review/rebalance principal: cada 10 sesiones;
- promoción automática: deshabilitada;
- PAPER/LIVE: deshabilitados;
- evidencia prospectiva nueva: solo desde el genesis completo del epoch actual.

Miembros:

- `weekly_opportunity_ridge`;
- `breakout_protected_by_floor`;
- `mean_reversion_floor_w1`;
- `cross_horizon_asymmetry`;
- `capital_allocation_challenger`;
- `benchmark_spy`;
- `benchmark_equal_weight`.

`cross_horizon_asymmetry` sigue medido como diagnóstico, pero está excluido del capital allocator mientras su evidencia OOS sea débil.

El Weekly Opportunity v9 usa un target ajustado por el contrato de 61 bps. El account state es continuo: los folds/reentrenamientos de walk-forward no reinician cash, posiciones, costos pagados ni historial.

La revisión de promoción requiere, entre otros gates configurados:

- al menos 63 sesiones;
- al menos 10 trades;
- drawdown absoluto máximo de 15%;
- Sharpe mínimo de 0.5;
- exceso positivo vs SPY;
- exceso positivo vs equal-weight;
- control de turnover en ventana de 63 sesiones.

Ver [docs/strategy_league.md](docs/strategy_league.md).

### `strategy_live` no significa LIVE trading

El workflow `strategy_live.yml` toma marcas intradía para observar cómo evolucionaría la Strategy League entre cierres. Corre durante horario de mercado y persiste snapshots compactos en:

- `strategy-live-base-v1`: base oficial exportada por EOD;
- `strategy-live-v1`: snapshot intradía observacional.

Estos snapshots:

- no envían órdenes;
- no habilitan PAPER;
- no habilitan LIVE;
- no cuentan como evidencia prospectiva oficial;
- no promueven estrategias.

La evidencia prospectiva oficial de la liga se actualiza en EOD.

## Fuentes de verdad y persistencia

### Contrato de ownership

`config/resource_ownership.json` define writers autorizados y CI lo valida con:

```bash
python -m utils.resource_ownership
```

| Recurso | Fuente / tag | Writers autorizados |
| --- | --- | --- |
| Runtime operacional | `runtime-state-v1` | EOD, ingest, intraday, retraining gobernado, bootstrap y workflows explícitamente autorizados |
| Checkpoints | `checkpoint-state-v1` | `intraday_engine`, `eod` |
| Monitoring | `monitoring-state-v1` | `monitoring` |
| Research durable | `research-state-v1` | `capital_challenger_tournament`, `walk_forward_oos` |
| Champions de producción | `data/training/models/*.json` | **solo `retrain_execute`** |
| GitHub Pages | deployment de `site/` | **solo `pages`** |
| History rewrite | Git refs | **solo compactación manual con leases exactos** |

Los estados observacionales de `strategy_live` tienen su propio writer dedicado: `strategy_live.yml` + `scripts/strategy_live_state.sh`.

Agregar un writer nuevo a un recurso autoritativo sin actualizar deliberadamente el contrato hace fallar CI.

### Runtime

`scripts/runtime_state.sh` publica generaciones inmutables sobre el tag `runtime-state-v1`:

```text
floor-runtime-state.tar.gz.run-<run_id>-a<attempt>
floor-runtime-state.tar.gz.run-<run_id>-a<attempt>.sha256
floor-runtime-state.tar.gz.run-<run_id>-a<attempt>.metadata.json
```

El runtime incluye estado operacional como market data, predictions, signals, orders/trades paper si existieran, snapshots, reports, metrics y persistence DB. No incluye los champions gestionados por Git.

La publicación:

- exige restore previo;
- verifica checksum y estructura;
- usa CAS contra el parent restaurado;
- protege el checkpoint frontier;
- rechaza archivos inesperados/symlinks;
- conserva las dos generaciones completas más recientes.

### Checkpoints

`checkpoint-state-v1` contiene marcadores ligeros de idempotencia para que el scheduler pueda decidir si un checkpoint ya fue procesado sin restaurar todo el runtime.

### Monitoring

`monitoring-state-v1` contiene `public_metrics.json` y se mantiene aislado del runtime autoritativo.

### Research

`research-state-v1` conserva evidencia durable no autoritativa:

- `strategy.json`;
- `strategy_attribution.json`;
- `walk_forward_oos.json`.

Otros experimentos pueden permanecer únicamente como artifacts de Actions si no forman parte de ese contrato durable.

## GitHub Actions

### Operación de mercado

- **`intraday_engine.yml`**: polling cuatro veces por hora durante la ventana UTC configurada; el gate decide checkpoints válidos de mercado, restaura runtime solo bajo writer lock y publica runtime + checkpoint state.
- **`eod.yml`**: backstop de cierre; genera forecast canónico, reconcilia predicciones maduras, actualiza Strategy League, exporta la base intradía, publica runtime/checkpoints y después solicita Pages.
- **`monitoring.yml`**: genera health snapshot aislado; no reescribe runtime.
- **`strategy_live.yml`**: marking intradía observacional de Strategy League.
- **`scheduler_watchdog.yml`**: backstop horario que despacha solo polls faltantes cuando no existe una ejecución reciente o activa.
- **`ingest.yml`**: refresh manual/controlado de market data sin convertir la ingesta en una decisión de trading.
- **`runtime_checkpoint_repair.yml`**: reparación explícita de gaps de checkpoints.

Los workflows de mercado no hacen trabajo pesado indiscriminadamente en cada push a `main`; gates, schedules y watchdog limitan duplicados.

### Gobierno de modelos

- **`retrain_assessment.yml`**: evaluación de necesidad de retrain según política y drift.
- **`retrain_execute.yml`**: única autoridad de promoción de champions.
- **`manual_train_all_models.yml`**: entrenamiento experimental/artifact-only.
- **`robust_range_v3.yml`**: benchmark/auditoría de candidatos de rango; no equivale por sí mismo a promoción productiva.
- **`manual_retrain_backtest_report.yml`**: reporte manual de retrain/backtest.

La política de retraining vive en `config/retraining.yaml`; actualmente la revisión nominal es quincenal.

### Research y replay

- **`walk_forward_oos.yml`**: walk-forward con retraining por folds y cuenta continua.
- **`capital_challenger_tournament.yml`**: tournament point-in-time del capital allocator.
- **`research_strategy_closeout.yml`**: evaluación retrospectiva de challengers de research sin mutar Strategy League v9.
- **`retrospective_replay.yml`** y workflows asociados: evidencia histórica/replay, no evidencia prospectiva.
- **`experimento_db_audit.yml`**: auditorías experimentales de DB/datos.

Research histórico puede respaldar una decisión humana de promoción futura, pero nunca habilita PAPER/LIVE automáticamente.

## GitHub Pages

La fuente autoritativa del dashboard es **`site/`**, no `docs/`.

`pages.yml`:

1. decide si el upstream produjo evidencia apta para publicación;
2. fija una sola vez las generaciones de runtime, research, monitoring y strategy-live;
3. restaura exactamente esas generaciones;
4. construye `site/data/*.json`;
5. publica Strategy League, experiment observation y snapshot intradía;
6. valida coherencia, seguridad, JS y secretos;
7. bloquea forecasts accionables cuando fallan frescura, completitud o compatibilidad;
8. despliega `site/` mediante GitHub Pages.

`pages_source_guard.yml` vuelve a despachar el deployment autoritativo cuando detecta interferencia del mecanismo legacy de Pages.

`docs/` contiene documentación y copias/activos históricos; no es la fuente de verdad del dashboard ni de datos de inversión.

### Pronósticos → Vista rápida

`site/forecasts.html` permite filtrar por ticker, horizonte y cobertura mínima.

**Vista rápida** conserva como default **Mayor cobertura**, y permite ordenar por:

- mayor cobertura;
- más cargado a la derecha;
- más cargado a la izquierda;
- más balanceado;
- mayor upside;
- mayor downside;
- rango más amplio;
- rango más estrecho;
- mayor `skill vs dummy`;
- ticker A–Z.

El sesgo se calcula como:

```text
sesgo = upside - abs(downside)
```

La amplitud se calcula como:

```text
amplitud = upside + abs(downside)
```

El criterio elegido se aplica a las tarjetas y a **Comparar activos**. Los valores faltantes quedan al final. Sesgo, amplitud y upside/downside describen la geometría del forecast; no son alpha ni recomendaciones de compra/venta.

## CI y auditoría E2E

`ci.yml` valida:

- schema de champions;
- challenger/epoch congelado;
- Ruff;
- mypy;
- pytest;
- cobertura mínima de 60%;
- smoke E2E;
- dry-run completo de Pages;
- resource ownership;
- contratos operativos del repo.

`e2e_yahoo_pages_audit.yml` añade una auditoría aislada de extremo a extremo:

```text
Yahoo real
  -> market data
  -> dataset modelable
  -> forecasts
  -> Strategy League
  -> Pages local
  -> Chromium
```

Ese workflow verifica coherencia y seguridad, toma screenshots y sube evidencia como artifact. No publica órdenes, PAPER ni LIVE.

## Desarrollo local

Instalación para desarrollo:

```bash
python -m pip install -e '.[dev]'
```

Checks principales:

```bash
ruff check src tests scripts
mypy --ignore-missing-imports src
pytest -q --cov=src --cov-report=term-missing --cov-fail-under=60
python scripts/validate_repo.py
```

Smoke reducido:

```bash
python scripts/validate_repo.py --smoke
```

Dependencias de modelado:

```bash
python -m pip install -e '.[modeling]'
```

CLI principal:

```bash
floor run-cycle
floor review-training
floor reconcile-predictions
floor build-site
```

## Bootstrap operativo

Ver [docs/00_guia/WORKFLOW_BOOTSTRAP.md](docs/00_guia/WORKFLOW_BOOTSTRAP.md).

Una instalación nueva debe reconstruir schemas/cache, ingerir mercado y producir los artifacts/estado requeridos antes de operar los workflows dependientes. El estado generado no debe incorporarse indiscriminadamente a Git.

## Seguridad y límites

- `config/strategies.yaml` mantiene `paper_execution_enabled: false` y `live_execution_enabled: false`.
- Las estrategias registradas tienen PAPER/LIVE deshabilitado.
- El risk gateway incluye límites y kill-switches, pero su existencia no habilita ejecución.
- Strategy League es shadow-paper y no tiene promoción automática.
- `strategy_live` es solo observacional.
- Pages escanea secretos y aplica CSP antes del deployment.
- El `GITHUB_TOKEN` de workflows no tiene por diseño autoridad administrativa para configurar branch protection ni el Source de Pages; esos settings se gestionan desde GitHub Settings.
- Ver [docs/paper_trading_gate.md](docs/paper_trading_gate.md) y [docs/live_trading_gate.md](docs/live_trading_gate.md) antes de cualquier cambio de modo.

## Releases y cambios destructivos

Antes de integrar cambios operativos usa [docs/release_checklist.md](docs/release_checklist.md).

La compactación de historia es manual-only. Usa leases exactos y `--atomic`; nunca debe sustituirse por force/prune indiscriminado.

Más detalle en [docs/storage_architecture.md](docs/storage_architecture.md).

## Estructura útil

```text
.github/workflows/      orquestación, gates y auditorías
config/                 contratos de modelos, costos, riesgo, strategies y ownership
scripts/                persistencia en Releases y validación operativa
src/contracts/          contratos compartidos
src/features/           features, labels y datasets
src/models/             training, calibration, gates y artifacts
src/forecasting/        serving y generación de forecasts
src/floor/              pipeline canónico, persistencia y reconciliación
src/strategies/         estrategias y activation gates
src/league/             Strategy League prospectiva e intraday marking
src/backtest/           backtester, costos y métricas
src/replay/             replay point-in-time y walk-forward
src/execution/          gateway PAPER/LIVE y risk controls
src/portfolio/          allocation/risk
src/monitoring/         health, drift y retraining policy
src/storage/            market DB, history y exports
src/notifications/      adapters de notificación
src/reporting/          reportes
src/utils/              auditorías, publication guards y workflow guards
site/                   frontend autoritativo de GitHub Pages
docs/                   documentación y material histórico
tests/                  contratos y regresiones
```
