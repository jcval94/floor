# Estado de alistamiento y orden recomendado de Workflows

Esta guía resume **qué debe existir** para decir que el repo ya está “generando contenido” (DB + modelos) y en qué orden conviene ejecutar GitHub Actions.

## Checklist de alistamiento

### 1) Base de datos local de mercado
Debe existir este archivo local:

- `data/market/market_data.sqlite`

Cómo crearlo:

```bash
make init-dbs
```

Cómo poblarlo:

```bash
make yahoo-ingest
```

Resultado esperado:

- La tabla `daily_bars` debe tener filas (`COUNT(*) > 0`).

> Nota: en entornos con restricciones de red/proxy, `yahoo-ingest` puede no descargar datos y dejar la DB vacía.

### 2) Base de datos de persistencia operativa
Debe existir este archivo local:

- `data/persistence/app.sqlite`

Cómo crearlo:

```bash
make init-dbs
```

### 3) Dataset modelable
Debe existir:

- `data/training/modelable_dataset.json`

Cómo generarlo desde la DB de mercado:

```bash
make build-training-from-db
```

### 4) Modelos entrenados en repo
Deben existir (sin `.gitkeep`):

- `data/training/models/value_champion.json`
- `data/training/models/timing_champion.json`

Cómo generarlos:

- Local: `bash scripts/retrain_models.sh data/training/modelable_dataset.json data/training first-run`
- Workflow: `retrain_execute` con `force=true`

## Orden recomendado de Workflows (primera puesta en marcha)

1. `db_bootstrap` (manual, una sola vez)  
   Al lanzarlo por `workflow_dispatch`, crea `data/market/market_data.sqlite` y `data/persistence/app.sqlite` y deja un marker para no volver a correr automáticamente.
2. `ingest`  
   Inicializa el contenido operativo con datos de mercado y artefactos de ciclo.
3. `retrain_execute` (`force=true`)  
   Construye dataset + reentrena + reemplaza artefactos en `data/training/models/`.
4. `intraday_engine`  
   Ejecuta el ciclo intradía usando los modelos ya presentes.
5. `eod`  
   Consolida reportes diarios y payload de `site/data`.
6. `monitoring`  
   Publica métricas operativas básicas.
7. `pages`  
   Publica el sitio estático con los datos más recientes.
8. `archive` (opcional diario)  
   Compacta índices de datos efímeros.


## Preflight SQLite obligatorio en workflows críticos

Los workflows `ingest`, `intraday_engine` y `retrain_execute` ejecutan un preflight común al inicio del job crítico:

1. `make init-dbs` para asegurar creación de `data/market/market_data.sqlite` y `data/persistence/app.sqlite`.
2. Saneamiento de permisos para runners:
   - directorios `data/market` y `data/persistence` con `u+rwx`;
   - archivos dentro de esas rutas con `u+rw`.
3. Validación de esquema con `sqlite3`:
   - en market, existencia de tabla `daily_bars`;
   - en persistence, existencia de tablas `predictions`, `signals`, `orders`, `training_reviews`.
4. Preflight de contenido para predicción/retraining (`intraday_engine` y `retrain_execute`):
   - si `SELECT COUNT(*) FROM daily_bars` devuelve `0`, se registra `::warning::` explícito;
   - se dispara ingesta Yahoo antes de continuar con ejecución intradía o reentrenamiento.

Este preflight evita fallos por SQLite inexistente/vacía o por permisos insuficientes de escritura en GitHub-hosted runners.

## Cadencia sugerida después del arranque

- Intradía continuo: `ingest` + `intraday_engine` + `eod` + `monitoring` + `pages` (sin `db_bootstrap`, porque es one-shot).
- Gobierno de modelos:
  - `retrain_assessment` quincenal (ya calendarizado).
  - `retrain_execute` cuando el assessment indique `RETRAIN` o bajo operación manual controlada.

## Workflows deprecados

No usar para operación nueva:

- `training-review` (usar `retrain_assessment`)
- `intraday` (usar `intraday_engine`)


## Ejecución manual (una sola vez)

1. Ir a **Actions** → `db_bootstrap`.
2. Click en **Run workflow**.
3. El job crea las dos SQLite y registra `data/snapshots/workflow_runs/db_bootstrap.json`.
4. En ejecuciones futuras, el mismo workflow se auto-salta con `reason=already_bootstrapped`.
