# Auditoría crítica de `floor` — 2026-08-21

> **Estado de la auditoría:** crítica / stop-the-line  
> **Veredicto:** **NO-GO para habilitar trading LIVE** hasta cerrar todos los P0.  
> **Alcance:** revisión estática de arquitectura, workflows, persistencia, forecasting, entrenamiento/retraining, señales, órdenes, risk controls, monitoring, Pages, CI/tests y artefactos actuales versionados.  
> **Nota:** el repositorio observado sigue operando en modo PAPER; esta auditoría no afirma que exista hoy un broker LIVE conectado.

---

## 1. Resumen ejecutivo

El repositorio tiene bastante más implementación real que en la auditoría histórica de marzo, pero actualmente hay varios problemas sistémicos que hacen que un workflow verde o un dashboard actualizado **no equivalgan a un sistema sano**.

Los problemas más graves no son de estilo ni de cobertura: afectan la **semántica de las predicciones, la confiabilidad del monitoring, la separación entre model/risk/execution y la integridad del estado operativo**.

Los hallazgos principales son:

1. `monitoring.yml` escribe `status: "OK"` con `series: []` cada 30 minutos sin medir el sistema.
2. `ingest.yml` ejecuta `run-cycle --event OPEN` cada 30 minutos durante cualquier día hábil, incluso fuera del OPEN real.
3. El dashboard actual contiene predicciones etiquetadas `OPEN` a las `19:47 ET`, horas después del cierre regular de mercado.
4. El pipeline principal crea señales/órdenes sin pasar por el stack de `strategies`, `portfolio_allocator`, `risk.yaml` ni `paper_executor`.
5. El control de stale data es fail-open: >7 días solo produce warning y la inferencia continúa.
6. Una Google Sheet externa puede reemplazar directamente la acción BUY/SELL/HOLD del modelo sin autenticidad, freshness ni confidence gate.
7. La inferencia d1/w1/q1 no ejecuta la lógica entrenada de los champions clásicos: reduce cada modelo a `floor_delta`/`ceiling_delta` globales.
8. Las probabilidades/confidences usadas por forecasting son transformaciones heurísticas, no probabilidades calibradas demostradas.
9. `retrain_assessment` intenta construir el dataset desde un SQLite que está ignorado por Git y no existe en un checkout limpio; esto puede convertir falta de datos en falso drift RED.
10. El último review versionado dice `ALERT / RETRAIN_NOW`, mientras producción sigue reportando champions con versión `20260318T223030Z`.
11. Git está funcionando simultáneamente como repositorio de código, model registry, log operativo y almacén de snapshots. El metadata del repo ya ronda **3.8 GB**.
12. Workflows distintos escriben a `main` con concurrency groups distintos, por lo que no existe exclusión mutua global entre ingest/monitoring/intraday/EOD/archive/retrain.

### Recomendación inmediata

Mantener `LIVE_TRADING_ENABLED=false` y **no interpretar `public_metrics.status == OK` como health real**. Antes de optimizar modelos o añadir nuevas estrategias, hay que estabilizar la plataforma en este orden:

1. corregir orquestación y semántica temporal;
2. hacer monitoring real y fail-closed;
3. unificar el camino signal → risk → order → execution;
4. arreglar train/serve parity y calibración;
5. corregir state management/retraining;
6. recién después mejorar performance del modelo.

---

# 2. Severidades

- **P0 — Stop the line:** puede producir decisiones operativas incorrectas, ocultar una falla crítica o saltarse controles esperados. Debe corregirse antes de LIVE.
- **P1 — Alta:** degrada confiabilidad, reproducibilidad, disponibilidad, costo operativo o capacidad de recuperación.
- **P2 — Media:** deuda de ingeniería/seguridad que debe endurecerse después de estabilizar P0/P1.

---

# 3. Hallazgos P0

## P0-01 — Monitoring fabrica un estado sano

**Evidencia**

Archivo: `.github/workflows/monitoring.yml`

El workflow corre cada 30 minutos y genera literalmente:

```json
{
  "generated_at": "<now>",
  "status": "OK",
  "series": []
}
```

El archivo actual `data/metrics/public_metrics.json` contiene exactamente un `status: "OK"` y una serie vacía.

**Impacto**

- Un error en ingest, forecasting, drift, persistence o Pages puede coexistir con un monitor en `OK`.
- Cualquier dashboard/alerta que consuma este campo hereda una falsa señal de salud.
- Además, `generated_at` cambia siempre, por lo que el workflow fabrica un commit aunque no haya información operacional nueva.

**Corrección requerida**

- Eliminar cualquier `status = OK` hardcodeado.
- Calcular health desde checks reales: data freshness, coverage, model availability/age, checkpoint completeness, persistence integrity, reconciliation, drift y workflow failures.
- Política fail-closed: `UNKNOWN` o `DEGRADED` cuando no existe evidencia suficiente para `OK`.
- No commitear heartbeat cada 30 minutos a Git.

**Criterio de cierre**

`OK` solo puede emitirse si un conjunto explícito y testeado de checks críticos está GREEN.

---

## P0-02 — `ingest` ejecuta inferencia `OPEN` fuera del OPEN real

**Evidencia**

Archivo: `.github/workflows/ingest.yml`

- schedule: `*/30 * * * 1-5`
- gate: `--kind always_open_day`
- ejecución: `python -m floor.main run-cycle --event OPEN`

Archivo: `src/utils/workflow_guards.py`

`always_open_day` solo comprueba que sea un día bursátil; **no valida una ventana horaria de mercado**.

Artefacto actual: `data/reports/dashboard.json`

- `generated_at`: `2026-08-21T23:47:27Z`
- AAPL `as_of`: `2026-08-21T19:47:27-04:00`
- `event_type`: `OPEN`

19:47 ET está fuera del horario regular de NYSE/Nasdaq, pero el sistema lo etiqueta como OPEN.

**Impacto**

- Contaminación semántica de históricos por sesión.
- Métricas y reconciliación por checkpoint dejan de ser confiables.
- Puede duplicar predicciones que también genera `intraday_engine`.
- La evaluación futura puede mezclar observaciones de momentos distintos bajo la misma categoría `OPEN`.

**Corrección requerida**

- `ingest` debe **ingerir datos**, no generar un `run-cycle` artificial.
- `intraday_engine` debe ser el único owner de OPEN/OPEN+2H/OPEN+4H/OPEN+6H/CLOSE.
- Si se requiere inferencia ad-hoc, usar un event type separado (`ADHOC`, `REFRESH`) que no contamine checkpoints canónicos.

**Criterio de cierre**

No debe existir ninguna predicción `event_type=OPEN` cuyo `as_of` esté fuera de la ventana definida para OPEN.

---

## P0-03 — El camino principal evita el stack de riesgo

**Evidencia**

Archivo: `src/floor/pipeline/intraday_cycle.py`

`maybe_build_order()` crea directamente:

```python
OrderRecord(
    symbol=signal.symbol,
    action=signal.action,
    qty=1,
    order_type="MKT",
    mode=mode,
)
```

El flujo llamado por `src/floor/main.py` no invoca:

- `src/strategies/run_strategies.py`
- `src/strategies/portfolio_allocator.py`
- `src/execution/run_paper_trade.py`
- `config/risk.yaml`

**Impacto**

Los controles declarados en `config/risk.yaml` no protegen necesariamente el camino que genera `data/orders/*.jsonl`:

- max position notional;
- max gross exposure;
- max single-name weight;
- max sector weight;
- daily loss limit;
- stale-data kill switch;
- reject-spike kill switch.

Esto crea una falsa sensación de seguridad: los controles existen en configuración, pero no son un gateway obligatorio.

**Corrección requerida**

Construir **un solo camino canónico**:

```text
forecast
  -> signal
  -> strategy decision
  -> centralized risk engine
  -> approved order
  -> paper/live execution adapter
  -> reconciliation
```

Ninguna orden debe poder persistirse o enviarse sin pasar por el risk engine.

**Criterio de cierre**

Un test E2E debe demostrar que una orden que viola cada límite de `risk.yaml` es bloqueada en el camino real usado por `floor.main`.

---

## P0-04 — Stale market data es fail-open

**Evidencia**

Archivo: `src/floor/pipeline/intraday_cycle.py`

La validación calcula `age_days` y para filas con más de 7 días solo incrementa `stale`; posteriormente hace `logger.warning(...)` y continúa.

Archivo: `config/risk.yaml`

`stale_data` aparece explícitamente como trigger del kill switch.

**Impacto**

El sistema puede producir forecasts/signals con datos materialmente obsoletos aunque la configuración declare stale data como condición de kill switch.

**Corrección requerida**

- Freshness threshold específico por fuente y sesión.
- Hard block para generación de señales/órdenes si se supera el umbral.
- Diferenciar `market data timestamp`, `fetched_at` y `as_of de inferencia`.

**Criterio de cierre**

Una fila stale debe terminar el ciclo antes de signal/order emission y reflejar `DEGRADED/FAILED` en monitoring.

---

## P0-05 — Google Sheets puede sobrescribir BUY/SELL/HOLD sin control suficiente

**Evidencia**

Archivo: `src/floor/external/google_sheets.py`

- descarga una URL configurada en env;
- valida solo columnas `symbol, action, confidence, note`;
- no hay firma/HMAC, allowlist de host, timestamp/freshness, version id ni límite de tamaño;
- captura cualquier excepción y devuelve `[]` silenciosamente.

Archivo: `src/floor/pipeline/intraday_cycle.py`

```python
if symbol in external and external[symbol].action in {"BUY", "SELL", "HOLD"}:
    signal.action = external[symbol].action
```

La `confidence` externa no es un gate para el override.

**Impacto**

Una fuente externa mutable puede convertir una decisión del modelo en BUY/SELL/HOLD directamente. En modo LIVE futuro, este diseño sería una frontera de seguridad insuficiente.

**Corrección requerida**

- Dejar de hacer override directo.
- Tratar el input externo como **feature/advisory signal**, no como autoridad final.
- Validar schema, domain allowlist, freshness, confidence, symbol universe y provenance.
- Fail closed y telemetría explícita si la fuente no está disponible.

**Criterio de cierre**

Una modificación externa no puede por sí sola cambiar una orden de HOLD a BUY/SELL sin atravesar model/strategy/risk policy.

---

## P0-06 — Train/serve skew en d1/w1/q1

**Evidencia**

Archivo: `src/models/train_classic_horizons.py`

El entrenamiento implementa lógica específica por familia, por ejemplo EVT con:

- buckets de volatilidad;
- trend buckets;
- `params.table`;
- `vol_cuts`;
- CV en retrain;
- además existen implementaciones de familias lineales/boosted.

Los champions actuales, por ejemplo `data/training/models/d1_champion.json`, contienen parámetros de régimen como `v1:up`, `v2:down`, `v3:down` y `vol_cuts`.

Archivo: `src/forecasting/load_models.py`

`_predict_classic_horizon()` clasifica la familia, pero calcula el forecast con:

```python
floor_delta = artifact.get("floor_delta")
ceiling_delta = artifact.get("ceiling_delta")
floor = close * (1.0 - floor_delta)
ceiling = close * (1.0 + ceiling_delta)
```

No usa los parámetros entrenados de régimen del champion para la predicción d1/w1/q1.

**Impacto**

- El modelo que se evalúa/selecciona en training no es el mismo comportamiento que se ejecuta en serving.
- Las métricas del champion dejan de describir fielmente la inferencia de producción.
- Cambiar de familia puede afectar principalmente timings hardcodeados y no la función predictiva que se creyó entrenar.

**Corrección requerida**

Implementar un predictor por familia que deserialize y ejecute exactamente los parámetros entrenados; idealmente reutilizar la misma función predictiva en training validation y serving.

**Criterio de cierre**

Golden test: para un mismo artifact + fixture de features, el output del predictor usado durante evaluación y el predictor de producción debe ser idéntico dentro de tolerancia numérica.

---

## P0-07 — “Confidence” y breach probability son heurísticos pero se usan como probabilidades

**Evidencia**

Archivo: `src/forecasting/load_models.py`

Para horizons clásicos:

```python
breach_prob = clamp(0.2 + spread_mae / close)
```

Archivo: `src/forecasting/generate_forecasts.py`

```python
confidence = 0.55 + 0.25 * ai_weight + 0.2 * (1 - d1.breach_prob)
```

Archivo: `src/floor/pipeline/intraday_cycle.py`

La confidence por horizon se deriva como `1 - breach_prob` y luego participa en la decisión BUY/SELL/HOLD.

**Impacto**

Los valores tienen apariencia probabilística (`0..1`) pero no hay evidencia en este camino de que sean probabilidades calibradas. Esto vuelve frágiles los thresholds y puede inducir interpretaciones erróneas en dashboard/estrategias.

**Corrección requerida**

- Renombrar heurísticas si van a mantenerse (`risk_score`, `quality_score`).
- Si se necesita probability/confidence, entrenar/calibrar explícitamente y medir Brier/log-loss/reliability curves fuera de muestra.
- Versionar el calibrador junto al champion.

**Criterio de cierre**

No mostrar ni consumir un campo llamado `probability`/`confidence` como probabilidad si no existe un contrato de calibración y métricas OOS.

---

## P0-08 — El assessment de retraining puede evaluar un dataset vacío

**Evidencia**

Archivo: `.github/workflows/retrain_assessment.yml`

El primer paso crítico es construir el dataset desde:

```text
data/market/market_data.sqlite
```

Pero:

- `.gitignore` excluye `data/market/*.sqlite`;
- el árbol actual de `data/market/` contiene solo `.gitkeep`;
- `retrain_assessment.yml` no ejecuta `make init-dbs`, restore de artifact ni Yahoo ingest antes de construir el dataset;
- `storage.market_db.load_daily_bars()` devuelve `[]` si el DB path no existe.

El `review_summary_latest.json` actual reporta:

- `suite_status = ALERT`
- `suite_recommendation = RETRAIN_NOW`
- schema RED;
- una lista enorme de `removed_columns`;
- `current_metrics = {}`;
- `insufficient_rows = 1`.

**Interpretación**

Esto es altamente consistente con un review ejecutado sobre un dataset vacío o insuficiente, no con una medición válida de drift real.

**Impacto**

- Falsos positivos de drift.
- Auto-retrain disparado por ausencia de datos.
- Monitoring/model governance deja de distinguir “no tengo evidencia” de “el modelo degradó”.

**Corrección requerida**

El assessment debe restaurar/crear su fuente de datos, validar row count, cobertura, último timestamp y schema **antes** de computar drift.

**Criterio de cierre**

Con dataset vacío/insuficiente el resultado debe ser `INSUFFICIENT_DATA`, nunca `RED/RETRAIN_NOW`.

---

## P0-09 — Model governance está contradictorio con el serving actual

**Evidencia**

- `data/training/review_summary_latest.json` (2026-08-15): `ALERT / RETRAIN_NOW` para `value` y `timing`.
- `data/reports/dashboard.json` (2026-08-21): las predicciones continúan reportando versiones `20260318T223030Z` para d1/w1/q1/value/timing.
- En el historial de commits se observan assessments quincenales, pero no se observó un commit con el mensaje de publicación esperado por `retrain_execute.yml` (`chore: retrain selected model artifacts`) después de marzo.

**Impacto**

No existe una historia coherente y verificable de:

```text
review -> trigger -> retrain -> challenger evaluation -> promotion/no-promotion -> serving version
```

**Corrección requerida**

- Generar un `model_governance_event.jsonl` canónico por cada ciclo.
- Registrar `review_id`, dataset hash/as_of, task, champion_before, challenger, decision, reason, champion_after, workflow_run_id.
- Alertar si `RETRAIN_NOW` permanece activo más de N horas/días sin resolución.

**Criterio de cierre**

Debe poder reconstruirse de forma determinista por qué cada champion servido es el champion vigente.

---

# 4. Hallazgos P1

## P1-01 — Git se usa como operational datastore y el repo ya ronda 3.8 GB

Los workflows hacen commits frecuentes de `data/`, modelos, snapshots, métricas y reportes. `.gitattributes` además fuerza artefactos JSON de training a Git no-LFS.

El problema no es solo el working tree: Git conserva el histórico completo de cada versión. Borrar/compactar archivos futuros no recupera automáticamente el tamaño del historial.

**Riesgos:** clones lentos, Actions más lentas, mayor probabilidad de non-fast-forward, mantenimiento difícil y crecimiento indefinido.

**Corrección:** separar código/config/model metadata de artefactos operativos voluminosos; aplicar una política explícita de retención y storage.

---

## P1-02 — `archive.yml` no archiva nada

El workflow se llama `archive`, pero únicamente reescribe diariamente:

```json
{"ts": "<now>", "status": "ok"}
```

No compacta, rota, mueve ni elimina históricos.

Además genera otro commit periódico.

**Corrección:** implementar lifecycle real o eliminar el workflow para no simular una capacidad inexistente.

---

## P1-03 — Carreras entre workflows que escriben a `main`

`ingest`, `monitoring`, `intraday_engine`, `eod`, `archive` y `retrain` usan concurrency groups diferentes y varios tienen `contents: write`.

Los groups solo serializan ejecuciones dentro del mismo grupo, no entre workflows distintos.

**Impacto:** pushes non-fast-forward, rebase races y resultados intermitentes.

**Corrección:** un solo writer/orchestrator para estado versionado o eliminar Git como state store. Mientras exista Git write-back, usar una política de serialización global y retry común.

---

## P1-04 — `eod` desactiva en la práctica su propio concurrency lock

Archivo: `.github/workflows/eod.yml`

```yaml
concurrency:
  group: eod-${{ github.ref }}-${{ github.run_id }}
```

`github.run_id` es único por corrida, así que dos corridas EOD nunca comparten group. El workflow corre cada 15 minutos y usa una ventana de cierre de 25 minutos.

El marker reduce duplicados, pero dos runners paralelos pueden pasar el gate antes de que cualquiera escriba el marker.

**Corrección:** group estable por workflow/ref/session day y un mecanismo idempotente/atómico de lock.

---

## P1-05 — Cron intraday con ventanas DST superpuestas + cancel-in-progress

`intraday_engine.yml` mantiene cron windows para EDT y EST. Varias horas/minutos quedan solapados. A la vez usa `cancel-in-progress: true` y el engine puede tardar hasta 20 minutos mientras el schedule es cada 15 minutos.

**Riesgo:** ejecuciones redundantes y cancelación de una corrida válida por la siguiente corrida programada.

**Corrección:** un ticker cron barato + session-aware scheduler interno, o schedules no solapados con locking por checkpoint.

---

## P1-06 — El SQLite operativo es efímero entre runners

`.gitignore` excluye los SQLite de `data/market` y `data/persistence`; un checkout limpio no los contiene.

`ingest` ejecuta `make init-dbs` y vuelve a descargar **2 años** de Yahoo cada 30 minutos de día hábil.

**Impacto:** polling innecesario, dependencia excesiva del proveedor, mayor costo/latencia/rate-limit risk y poca continuidad real del state SQLite.

**Corrección:** usar almacenamiento durable para el market/state DB o construir una estrategia incremental explícita de restore/checkpoint.

---

## P1-07 — Retry de `ingest` puede terminar exitoso después de tres fallas

En `ingest.yml`:

```bash
for i in 1 2 3; do
  python ... && break || sleep $((i*20))
done
```

Si el tercer intento falla, el último comando ejecutado puede ser el `sleep`, que devuelve 0. Los validations posteriores pueden detectar varios casos, pero el retry en sí no conserva correctamente la falla final.

**Corrección:** `set -euo pipefail` y `exit 1` explícito al agotar intentos.

---

## P1-08 — Dos contratos de orden incompatibles (`qty` vs `quantity`)

`src/strategies/base.py::build_order_payload()` produce:

```json
{"qty": 123}
```

`src/execution/paper_executor.py::_create_orders()` consume:

```python
qty = int(s.get("quantity", 0))
```

Por tanto, conectar directamente el output del strategy pack al paper executor produce cantidad 0 y omite la orden.

Los tests de execution usan fixtures escritos directamente con `quantity`, por lo que no prueban la integración real strategies → execution.

**Corrección:** un schema canónico (`OrderIntent`) compartido y validado por Pydantic/dataclass/schema contract.

---

## P1-09 — Configuración de riesgo contradictoria

`config/risk.yaml` declara:

- `max_position_notional_usd: 50000`

Pero `config/strategies.yaml` permite, por ejemplo:

- `model_only.max_notional_usd: 60000`
- `consensus.max_notional_usd: 80000`

Como no existe un global risk gateway obligatorio, la contradicción no se resuelve de forma central.

**Corrección:** riesgo global siempre debe ganar sobre estrategia; validar configs al startup/CI.

---

## P1-10 — Site/operational reporting mezcla snapshots con edades incompatibles

El repo contiene:

- dashboard operativo actualizado en agosto;
- `site/data/drift.json` cuyo source principal sigue en 2026-03-12;
- `site/data/incidents.json` con incidente SEV2 de 2026-03-12;
- `public_metrics.json` actualizado cada 30 min pero sin series reales.

**Impacto:** la UI puede parecer reciente por un timestamp y contener subsistemas materialmente stale.

**Corrección:** cada payload debe exponer `source_as_of`, `generated_at`, freshness SLA y `is_stale`; Pages debe fallar/degradar por componente, no solo por el dashboard general.

---

## P1-11 — Fallback de forecast convierte fallo total en predicciones sintéticas persistidas

Si todos los forecasts quedan bloqueados, `intraday_cycle.py` crea bandas fallback ±0.5%, confidence 0.5 y las persiste como predicciones.

Aunque el expected return 0 normalmente conduce a HOLD, el histórico de predicciones mezcla output de modelo con output sintético.

**Corrección:** persistir un registro de `PredictionUnavailable`/`blocked`, no fabricar un forecast numérico salvo que esté explícitamente marcado como fallback y excluido de evaluación/trading.

---

# 5. Hallazgos P2 / hardening

## P2-01 — CI valida YAML de forma superficial

`scripts/validate_repo.py::validate_configs()` considera suficiente que el archivo tenga contenido y `:`. Esto no valida schema, tipos, rangos ni contradicciones entre configs.

**Corrección:** schemas tipados y cross-config validation.

## P2-02 — Secret scan produce warnings pero no bloquea CI

`review_secrets_and_permissions()` imprime WARN y no cambia `failed=True`.

**Corrección:** usar secret scanning dedicado y fallar ante credenciales verificables; mantener allowlist para falsos positivos.

## P2-03 — GitHub Actions usa tags de actions, no commits SHA pinneados

`actions/checkout@v4/v5`, `upload-artifact@v4`, etc. son normales, pero para una plataforma con permisos `contents: write` conviene pinnear SHA de terceros y usar Dependabot/Renovate.

## P2-04 — README histórico está desactualizado y contiene defectos ya corregidos

El README principal conserva un snapshot `2026-03-13` que todavía afirma, entre otras cosas, que:

- `normalize_model_tasks()` falla para strings;
- `summarize_modelable_rows()` devuelve `None`;
- no existen champions;
- no existen órdenes.

El código/árbol actuales contradicen esas afirmaciones. Este documento debe considerarse el audit vigente hasta que el README principal sea reescrito.

---

# 6. Riesgo arquitectónico principal: existen varios “sistemas” dentro del mismo repo

Hoy conviven al menos tres caminos parcialmente independientes:

### Camino A — pipeline principal

```text
Yahoo/SQLite
 -> features
 -> forecasting
 -> SignalRecord
 -> maybe_build_order(qty=1)
 -> JSONL + app.sqlite
```

### Camino B — strategies

```text
forecast rows
 -> 5 strategies
 -> portfolio_allocator
 -> order payload con `qty`
```

### Camino C — paper execution

```text
cycles/signals con `quantity`
 -> PaperExecutor
 -> Order/Fill/Trade/PortfolioSnapshot
```

El problema no es que haya módulos separados; el problema es que **no comparten un contrato canónico ni una frontera de riesgo obligatoria**.

La estabilización debe eliminar la posibilidad de que el camino A genere una orden saltándose B/C/risk.

---

# 7. Estado observado al 2026-08-21

| Componente | Estado observado | Evaluación |
|---|---|---|
| `data/metrics/public_metrics.json` | `OK`, `series=[]` | **No confiable** |
| `data/reports/dashboard.json` | actualizado 2026-08-21 | Actual, pero contiene `OPEN` fuera de horario |
| d1/w1/q1/value/timing champion version | `20260318T223030Z` | ~5 meses sin cambio visible |
| `review_summary_latest.json` | `ALERT / RETRAIN_NOW` | assessment probablemente contaminado por falta de DB |
| `data/market/` versionado | solo `.gitkeep` | SQLite no durable vía Git |
| `data/persistence/` versionado | solo `.gitkeep` | app.sqlite no durable vía Git |
| órdenes versionadas | INTC/ORCL históricas | PAPER, marzo 2026 |
| `LIVE_TRADING_ENABLED` en workflow principal | `false` | Correcto mantenerlo así |
| repo metadata size | ~3.8 GB | **Necesita lifecycle/hygiene** |
| `site/data/drift.json` | source 2026-03-12 | Stale |
| `site/data/incidents.json` | SEV2 2026-03-12 | Stale / no resolución actual demostrada |

---

# 8. Orden recomendado de remediación

## Fase 0 — Contención

1. Mantener LIVE deshabilitado.
2. Eliminar `run-cycle --event OPEN` de `ingest.yml`.
3. Cambiar monitoring a `UNKNOWN/DEGRADED` hasta que tenga checks reales.
4. Hacer stale-data fail-closed.
5. Desactivar override directo de Google Sheets sobre action.

## Fase 1 — Orquestación y estado

1. Definir un único owner por checkpoint.
2. Unificar locks/concurrency.
3. Separar artefactos operativos del historial Git.
4. Diseñar persistence durable e incremental.
5. Implementar lifecycle real; eliminar `archive.yml` falso.

## Fase 2 — Model correctness

1. Garantizar train/serve parity por familia.
2. Rehacer breach probability/confidence con calibración real o renombrar heurísticas.
3. Añadir model age/freshness gates.
4. Reparar retraining assessment para que nunca evalúe un dataset vacío.
5. Añadir governance trail champion/challenger.

## Fase 3 — Risk/execution

1. Crear `OrderIntent` canónico.
2. Hacer `RiskEngine` obligatorio.
3. Resolver `qty` vs `quantity`.
4. Aplicar límites globales sobre cualquier estrategia.
5. Hacer PAPER y futuro LIVE dos adapters detrás del mismo risk-approved order.

## Fase 4 — Observabilidad y CI

1. Health real con SLIs/SLOs.
2. Tests E2E de workflows críticos.
3. Tests de horario/freshness/DST.
4. Contract tests strategies → execution.
5. Config schemas + cross-validation.
6. Test golden de train/serve parity.

---

# 9. Tests que faltan antes de considerar el sistema estable

Como mínimo:

- `test_ingest_never_emits_open_forecast_outside_open_window`
- `test_stale_market_data_blocks_signal_emission`
- `test_stale_market_data_blocks_order_emission`
- `test_external_recommendation_cannot_bypass_risk`
- `test_risk_limits_are_enforced_in_floor_main_path`
- `test_daily_loss_kill_switch_blocks_orders`
- `test_global_notional_caps_override_strategy_caps`
- `test_strategy_order_contract_matches_executor_contract`
- `test_monitoring_cannot_report_ok_without_required_series`
- `test_empty_retraining_dataset_returns_insufficient_data`
- `test_retrain_review_has_valid_dataset_as_of_and_row_count`
- `test_train_serve_golden_fixture_evt`
- `test_train_serve_golden_fixture_xgboost`
- `test_model_probability_calibration_contract`
- `test_single_checkpoint_owner_per_session_event`
- `test_cross_workflow_write_serialization_or_no_git_writeback`
- `test_pages_marks_component_payload_stale_independently`

---

# 10. Definition of Done para quitar el NO-GO

No habilitar LIVE hasta que **todos** se cumplan:

- [ ] No existen eventos OPEN fuera de la ventana OPEN.
- [ ] Ingest no genera forecasts/signals/orders.
- [ ] Monitoring no contiene health hardcodeado.
- [ ] Stale data bloquea el ciclo.
- [ ] Toda orden pasa por un RiskEngine único.
- [ ] `risk.yaml` se aplica en tests E2E del camino real.
- [ ] Google Sheets no puede reemplazar directamente una acción final.
- [ ] Train y serving usan exactamente la misma lógica del champion.
- [ ] Probabilidades/confidences están calibradas o renombradas correctamente.
- [ ] Retraining assessment valida datos suficientes antes de medir drift.
- [ ] Existe trazabilidad completa review → retrain → promotion → serving.
- [ ] `qty/quantity` está unificado por schema.
- [ ] La persistencia no depende de un SQLite efímero creado desde cero en cada runner.
- [ ] Se elimina el crecimiento ilimitado de artifacts/heartbeats en Git.
- [ ] Los workflows escritores no pueden competir por `main`.
- [ ] CI contiene tests negativos para cada uno de estos controles.

---

# 11. Cosas que NO deben priorizarse todavía

Hasta cerrar los P0, **no recomiendo invertir tiempo significativo** en:

- agregar más familias de modelos;
- aumentar hiperparameter search;
- sumar más indicadores técnicos;
- construir nuevas estrategias;
- mejorar visualmente el dashboard;
- añadir ejecución LIVE.

El mayor retorno ahora está en **correctness, state, risk y observability**.

---

# 12. Conclusión

`floor` ya tiene piezas valiosas —feature engineering, forecasting multi-horizon, champion artifacts, reconciliation, paper execution, workflow guards y tests— pero esas piezas todavía no forman un sistema operativo con una única verdad de estado y una única frontera de riesgo.

La prioridad no debe ser “hacer el modelo más inteligente”; debe ser conseguir que el sistema sea **semánticamente correcto, fail-closed, auditable y determinista**.

Hasta entonces:

> **PAPER: permitido para desarrollo/validación.**  
> **LIVE: NO-GO.**
