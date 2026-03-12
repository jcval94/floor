# floor

Bootstrap operativo de una plataforma IA/Finanzas para estimar **floor/ceiling probabilísticos** en acciones de EEUU, producir señales accionables y operar ciclos intradía auditables en GitHub Actions.

## Qué incluye este bootstrap

- Arquitectura modular Python (`src/floor`) lista para iterar en producción ligera.
- Configuración centralizada en `/config` por dominio (universo, costos, riesgo, horizontes, sheets, notificaciones, pages).
- Convenciones de nombres y particionado para datasets, modelos, reportes y snapshots.
- Políticas de versionado de datos (qué sí/no guardar en el repo).
- Backlog por fases (MVP → robustecimiento → paper trading automatizado → broker real).
- Capa de visualización estática para GitHub Pages (`site/`, `site/assets/`, `site/data/`).

## Árbol de carpetas objetivo

```text
floor/
├── .github/workflows/
├── config/
├── data/
│   ├── predictions/
│   ├── signals/
│   ├── orders/
│   ├── trades/
│   ├── metrics/
│   ├── reports/
│   ├── snapshots/
│   └── training/
├── docs/
│   ├── 00_guia/
│   ├── 10_resumenes/
│   ├── 20_fuentes/
│   └── 01_bootstrap/
├── scripts/
├── site/
│   ├── assets/
│   └── data/
├── src/floor/
│   ├── external/
│   ├── modeling/
│   ├── pipeline/
│   ├── reporting/
│   └── training/
├── tests/
├── Makefile
├── pyproject.toml
└── README.md
```

## Guía rápida

```bash
make test
make run-cycle SYMBOLS=AAPL,MSFT EVENT=OPEN
make review-training
make build-site
```

## Documentación de diseño

Ver blueprint detallado en: `docs/01_bootstrap/BOOTSTRAP_PLAN.md`.


## Workflows de orquestación

- `ingest.yml`
- `intraday_engine.yml`
- `eod.yml`
- `retrain_assessment.yml`
- `retrain_execute.yml`
- `monitoring.yml`
- `archive.yml`
- `pages.yml`
