# Bootstrap operativo de FLOOR

Esta guía describe el flujo actual. SQLite y los snapshots generados **no se versionan en Git**; el estado durable operacional se restaura/publica mediante GitHub Releases.

## Requisitos

- Python compatible con `pyproject.toml`.
- `gh` autenticado para workflows/scripts que usan Releases.
- acceso de red a Yahoo Finance cuando se requiera refresh.
- champions de serving válidos en `data/training/models/*.json`.

## Bootstrap local

```bash
python -m pip install -e '.[dev]'
make init-db-schemas
make yahoo-ingest
```

Para reconstruir el dataset modelable:

```bash
make build-training-from-db
```

Para modelado instala también:

```bash
python -m pip install -e '.[modeling]'
```

## Bootstrap en GitHub Actions

No existe un `db_bootstrap` autoritativo separado. Los workflows crean schemas cuando los necesitan y restauran el runtime desde `runtime-state-v1`.

Orden recomendado para una instalación sin estado previo:

1. **ingest** — crea/actualiza market SQLite y publica runtime.
2. **retrain_assessment** — construye evidencia actual y decide si existe necesidad de retraining.
3. **retrain_execute** — solo cuando existe autorización explícita de promoción.
4. **intraday_engine** — ejecuta checkpoints programados.
5. **eod** — genera cierre canónico y reconciliación.
6. **monitoring** — publica health aislado.
7. **pages** — construye y despliega el snapshot auditado.

Una vez inicializado, `intraday_engine`, `eod` y `monitoring` funcionan por schedule. `scheduler_watchdog` solo repone polls ausentes; no duplica runs recientes/activos.

## Estado durable

### runtime-state-v1

Incluye, cuando existen:

- market SQLite;
- persistence SQLite;
- predictions/signals/orders/trades;
- snapshots y reportes;
- métricas;
- reviews de training.

Cada publicación nueva es una generación inmutable con checksum y metadata. El publisher verifica CAS contra la generación que restauró.

### checkpoint-state-v1

Contiene únicamente markers ligeros necesarios para gating. Permite decidir si un checkpoint está pendiente sin descargar el runtime completo.

### monitoring-state-v1

Snapshot JSON de health operacional. Es aislado del runtime writer.

### research-state-v1

Evidencia de research/strategy que puede superponerse para Pages sin convertirse en estado operacional autoritativo.

## Pages

El build de Pages fija al inicio los payloads de Release que utilizará. Esa selección queda registrada en el audit de publicación; un asset nuevo que aparezca durante el build no cambia el snapshot a mitad de ejecución.

El directorio autoritativo es `site/`. `docs/` no debe usarse como fuente de datos operativos.

## Writers autorizados

El inventario está en:

```text
config/resource_ownership.json
```

Valídalo con:

```bash
PYTHONPATH=src python -m utils.resource_ownership
```

## Validación

```bash
ruff check src tests scripts
mypy --ignore-missing-imports src
pytest -q
python scripts/validate_repo.py
```

## Settings de GitHub

Dos controles son administrativos y no pueden imponerse con el `GITHUB_TOKEN` normal:

1. **Pages → Source: GitHub Actions**.
2. **Ruleset/branch protection para `main`** con PR + CI requerido.

El código contiene guards para reducir el riesgo si esos settings aún no están configurados, pero los settings deben activarse desde la administración del repositorio.
