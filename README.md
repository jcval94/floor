# FLOOR

FLOOR es un sistema reproducible de forecasting probabilístico de pisos/techos para acciones, operado principalmente con GitHub Actions. El flujo productivo combina Yahoo Finance, SQLite efímero/restaurable, modelos champion versionados en Git, estado operacional en GitHub Releases y un dashboard estático en GitHub Pages.

## Arquitectura actual

```text
Yahoo Finance
  -> data/market/market_data.sqlite
  -> features + labels
  -> forecast multi-horizonte (d1 / w1 / q1 / m3)
  -> JSONL durable + SQLite cache
  -> runtime-state-v1
  -> audited Pages build
       + research-state-v1
       + monitoring-state-v1
       + champions de main
  -> site/
  -> GitHub Pages
```

Los archivos generados bajo `data/` y `site/data/` no son la fuente de verdad versionada en Git. El runtime durable vive en Releases; el registro de modelos servido sí vive en `data/training/models/*.json`.

## Fuentes de verdad y ownership

El contrato machine-readable está en `config/resource_ownership.json` y CI lo valida con `python -m utils.resource_ownership`.

| Recurso | Fuente / tag | Writers autorizados |
| --- | --- | --- |
| Runtime operacional | `runtime-state-v1` | intraday, EOD, ingest, retraining gobernado y workflows explícitamente declarados |
| Checkpoints | `checkpoint-state-v1` | `intraday_engine`, `eod` |
| Monitoring | `monitoring-state-v1` | `monitoring` |
| Research | `research-state-v1` | workflows de research declarados |
| Champions de producción | `data/training/models/*.json` | **solo `retrain_execute`** |
| GitHub Pages | `site/` | **solo `pages`** |
| History rewrite | Git refs | **solo workflow manual de compactación**, protegido con leases exactos |

Agregar un writer nuevo sin actualizar deliberadamente ese contrato hace fallar CI.

## Estado persistente

### Runtime

`scripts/runtime_state.sh` publica generaciones inmutables:

```text
floor-runtime-state.tar.gz.run-<run_id>-a<attempt>
floor-runtime-state.tar.gz.run-<run_id>-a<attempt>.sha256
floor-runtime-state.tar.gz.run-<run_id>-a<attempt>.metadata.json
```

Los lectores aceptan únicamente generaciones completas. Una subida interrumpida no sustituye el último snapshot válido. Se conservan las dos generaciones completas más recientes.

El metadata incluye generación, parent SHA-256, commit/workflow de origen y checkpoint frontier. La publicación usa CAS para rechazar writers basados en un parent obsoleto.

### Monitoring y research

Siguen el mismo patrón de generaciones inmutables. Los assets legacy se leen únicamente como ruta de compatibilidad durante la migración.

## Modelos

Los cinco artefactos de serving son:

- `d1_champion.json`
- `w1_champion.json`
- `q1_champion.json`
- `value_champion.json`
- `timing_champion.json`

`retrain_execute.yml` es la única autoridad de promoción a producción. `manual_train_all_models.yml` es experimental/artifact-only y no tiene permisos para modificar el registro de champions.

El dashboard toma la identidad de serving desde los artifacts actuales. Un `review_summary_latest.json` stale puede conservarse como evidencia histórica, pero no puede sobrescribir la versión del champion servido.

El benchmark de `robust_range_v3` se documenta en [docs/robust_range_v3.md](docs/robust_range_v3.md).

## GitHub Actions

### Operación de mercado

- `intraday_engine.yml`: polling programado de checkpoints de mercado.
- `eod.yml`: cierre, forecast canónico y reconciliación.
- `monitoring.yml`: health snapshot aislado.
- `scheduler_watchdog.yml`: backstop. Solo despacha si no existe una ejecución reciente o activa.
- `ingest.yml`: refresh manual de mercado sin mutar decisiones.

Los workflows de mercado **no corren por cada push a `main`**. Producción usa schedule/manual/watchdog para evitar fan-out de runs gate-only.

### Gobierno de modelos

- `retrain_assessment.yml`: evaluación gobernada e invalidación ante cambios relevantes.
- `retrain_execute.yml`: promoción explícita y autorizada.
- `manual_train_all_models.yml`: entrenamiento experimental sin escritura a producción.

### Pages

`pages.yml`:

1. verifica que el commit upstream esté contenido en el checkout actual;
2. resuelve una sola vez las generaciones de runtime/research/monitoring;
3. restaura exactamente esas generaciones fijadas;
4. construye y audita `site/data/*.json`;
5. bloquea forecast accionable si falla contrato/frescura/model compatibility;
6. despliega `site/`.

El audit publicado registra el commit y las generaciones de Release utilizadas, evitando que un build mezcle silenciosamente “lo último” de distintas fuentes durante su ejecución.

`docs/` no es una fuente autoritativa de datos de inversión.

## Persistencia SQLite

SQLite es cache/índice operacional, no fuente durable única. Los ciclos de forecast y replay reutilizan conexión/transacción por batch y la hidratación/reconciliación usa commits chunked.

Cuando falta el cache, se reconstruye desde los ledgers durables. Antes de publicar runtime se ejecuta `PRAGMA wal_checkpoint(TRUNCATE)` y `PRAGMA quick_check`.

## CI

El workflow `ci.yml` valida:

- schema de champions;
- challenger congelado;
- Ruff;
- mypy;
- pytest + cobertura mínima;
- smoke E2E;
- dry-run completo de Pages;
- resource ownership;
- contratos operativos del repo.

Comandos locales principales:

```bash
python -m pip install -e '.[dev]'
ruff check src tests scripts
mypy --ignore-missing-imports src
pytest -q
python scripts/validate_repo.py
```

Para modelado:

```bash
python -m pip install -e '.[modeling]'
```

## Bootstrap operativo

Ver [docs/00_guia/WORKFLOW_BOOTSTRAP.md](docs/00_guia/WORKFLOW_BOOTSTRAP.md).

En resumen: no se versionan SQLite ni runtime generado. Una instalación nueva crea schemas, ingesta Yahoo, construye/evalúa modelos y después publica estado durable en Releases.

## Releases y cambios

Antes de integrar cambios operativos usa [docs/release_checklist.md](docs/release_checklist.md).

Los cambios destructivos de history compaction son manual-only y usan `--force-with-lease=<ref>:<sha>` + `--atomic`. Nunca deben ejecutarse con wildcard force/prune sin lease.

## Seguridad y límites

- No hay live trading habilitado por estos workflows.
- Strategy League y experimentos publicados son shadow/paper salvo que una capa distinta lo autorice explícitamente.
- Pages escanea secretos y aplica CSP antes de deployment.
- El `GITHUB_TOKEN` de workflows no tiene por diseño autoridad administrativa para configurar branch protection o el Source de Pages. Esos dos settings del repositorio deben gestionarse desde GitHub Settings.

## Estructura útil

```text
.github/workflows/      orquestación
config/                 configuración + ownership
scripts/                helpers operativos
src/features/           features / labels
src/models/             training y artifacts
src/forecasting/        serving
src/floor/              pipeline y persistencia
src/monitoring/         health / drift
src/utils/              auditorías y publication guards
site/                   frontend estático
tests/                  contratos y regresiones
```
