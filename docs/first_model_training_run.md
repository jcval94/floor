# Primera ejecución para generar modelos en el repo

Este proyecto guarda y actualiza los modelos dentro de `data/training/models/`.

## Qué hace el workflow

El workflow `.github/workflows/retrain_execute.yml`:

1. Evalúa si debe reentrenar (o se fuerza con `force=true`).
2. Ejecuta `scripts/retrain_models.sh`.
3. **Reemplaza** el contenido de `data/training/models/` en cada ejecución.
4. Guarda métricas de entrenamiento en `data/training/metrics/training_metrics_<version>.json`.
5. Commitea y hace push de los artefactos en el mismo repo.

## Requisitos para la primera ejecución

1. Tener un dataset modelable en el repo, por ejemplo:
   - `data/training/modelable_dataset.json`
2. El dataset debe ser JSON con estructura:
   - `{"rows": [...]}` o `[...]`
   - filas con features esperadas por los modelos `m3` (`atr_14`, `trend_context_m3`, `drawdown_13w`, `dist_to_low_3m`, `ai_horizon_alignment`, etc.)
   - targets: `floor_m3` y `floor_week_m3`
   - idealmente columna `split` (`train`, `validation`/`test`)

## Opción A: correr en GitHub Actions (recomendado)

1. Ir a **Actions** → `retrain_execute`.
2. Click en **Run workflow**.
3. Inputs sugeridos:
   - `force`: `true` (primera vez)
   - `dataset_path`: `data/training/modelable_dataset.json`
   - `version`: opcional (si se omite se usa timestamp UTC)
4. Verificar al terminar:
   - `data/training/models/value_champion.json`
   - `data/training/models/timing_champion.json`
   - `data/training/metrics/training_metrics_<version>.json`

## Opción B: correr localmente (mismo flujo del workflow)

```bash
bash scripts/retrain_models.sh data/training/modelable_dataset.json data/training first-run
```

Eso genera/reemplaza artefactos en la misma carpeta del repo, listos para commit.


## Nuevo workflow manual recomendado (ABT con compuertas de calidad)

También puedes lanzar `.github/workflows/manual_train_all_models.yml`, diseñado para:

1. Refrescar mercado desde Yahoo y reconstruir el ABT completo (`modelable_dataset.json`).
2. Validar calidad del ABT antes de entrenar (mínimo de filas, mínimo de variables y columnas críticas obligatorias para m3).
3. Entrenar modelos por horizonte para `d1`, `w1`, `q1` y `m3` en una sola ejecución manual.
4. Generar logs detallados y un CSV consolidado (`d1,w1,q1,m3`) con resultados de entrenamiento, además de verificar champions y métricas antes de commitear artefactos.

Inputs clave del workflow:

- `min_rows` y `min_columns`: umbrales mínimos para exigir un ABT robusto.
- `yahoo_range` y `yahoo_interval`: ventana/frecuencia del refresco de mercado.
- `commit_artifacts`: permite activar/desactivar commit automático de artefactos.
- El workflow publica como artefacto el log de ejecución y un CSV de resultados por horizonte.

## Notas

- Cada reentrenamiento **reemplaza** la carpeta `data/training/models/` para asegurar que siempre vivan aquí los artefactos vigentes.
- El registro de métricas por versión queda en `data/training/metrics/`.
