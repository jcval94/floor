# BOOTSTRAP_PLAN

## 1) Árbol definitivo de carpetas

```text
floor/
├── .github/workflows/
│   ├── intraday.yml
│   ├── training-review.yml
│   └── pages.yml
├── config/
│   ├── universe.yaml
│   ├── costs.yaml
│   ├── risk.yaml
│   ├── sheets.yaml
│   ├── notifications.yaml
│   ├── horizons.yaml
│   └── pages.yaml
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
│   ├── index.html
│   ├── assets/
│   └── data/
├── src/floor/
│   ├── config.py
│   ├── calendar.py
│   ├── schemas.py
│   ├── storage.py
│   ├── main.py
│   ├── external/
│   │   └── google_sheets.py
│   ├── modeling/
│   │   └── contracts.py
│   ├── pipeline/
│   │   └── intraday_cycle.py
│   ├── training/
│   │   └── review.py
│   └── reporting/
│       └── generate_site_data.py
└── tests/
```

## 2) Módulos Python y responsabilidades

- `main.py`: CLI y orquestación de comandos (`run-cycle`, `review-training`, `build-site`).
- `calendar.py`: sesión NYSE, checkpoints operativos, manejo holiday/early close.
- `pipeline/intraday_cycle.py`: predicción multi-horizonte, señales, órdenes (paper/live guardrail).
- `modeling/contracts.py`: interfaz de modelo y campeón vigente; base para champion/challenger.
- `external/google_sheets.py`: ingestión de recomendaciones externas vía CSV público de Sheets.
- `training/review.py`: evaluación quincenal (data drift, concept drift, calibration drift, performance decay).
- `reporting/generate_site_data.py`: materialización de snapshots para GitHub Pages.
- `storage.py`: append auditable JSONL.
- `schemas.py`: contratos de datos del dominio.
- `config.py`: lectura de entorno y parámetros de runtime.

## 3) Convenciones de nombres

### Datasets

- Predicciones: `data/predictions/{YYYY}/{MM}/{DD}/{symbol}_{horizon}_{event}.jsonl`
- Señales: `data/signals/{YYYY}/{MM}/{DD}/{symbol}_{horizon}_{event}.jsonl`
- Órdenes: `data/orders/{YYYY}/{MM}/{DD}/{symbol}_{event}.jsonl`
- Trades: `data/trades/{YYYY}/{MM}/{DD}/{broker_account}.jsonl`
- Métricas: `data/metrics/{model_name}/{YYYYMMDD}.jsonl`

### Modelos

- Artefactos: `artifacts/models/{model_family}/{horizon}/{version}/`
- Convención de versión: `v{major}.{minor}.{patch}+{yyyymmdd}`

### Reportes y snapshots

- Reportes: `data/reports/{YYYY}/{MM}/{DD}/report_{session}.json`
- Snapshot dashboard: `data/snapshots/{YYYY}/{MM}/{DD}/dashboard.json`

### Páginas estáticas

- JSON consumido por UI: `site/data/{feed_name}.json`
- Assets: `site/assets/{css|js|img}/...`

## 4) Esquema de configuración en `/config`

- `universe.yaml`: universo, reglas de elegibilidad, rebalance mensual.
- `costs.yaml`: supuestos de comisiones, slippage y latency.
- `risk.yaml`: límites por posición, concentración, exposure gross/net.
- `sheets.yaml`: integración de input externo y validaciones.
- `notifications.yaml`: canales de alertas (Slack/Email/Webhook).
- `horizons.yaml`: definición explícita de `d1`, `w1`, `q1` y buckets/días.
- `pages.yaml`: fuentes de datos publicables y refresh policy.

## 5) Backlog inicial por fases

### Fase 1 — MVP investigable

- Dataset features/targets sin leakage.
- Baseline quantile + clasificación temporal calibrada.
- Evaluación offline y bitácora de experimentos.
- Publicación inicial de métricas y señales en `data/`.

### Fase 2 — Robustecimiento

- Validación walk-forward con ventanas rodantes.
- Monitoreo de drift y calibración continuo.
- Quality gates CI (tests, schema checks, reproducibilidad).
- Hardening de calendario y manejo de corner cases de mercado.

### Fase 3 — Paper trading automatizado

- Scheduler intradía cada 2h (incluye open/cierre).
- Simulador de ejecución con costos y latencia.
- Métricas de estrategia: hit ratio, PnL, max drawdown, turnover.
- Alertas operativas automáticas.

### Fase 4 — Preparación broker real

- Adapter broker desacoplado con modo `LIVE` explícito.
- Gestión de órdenes avanzada (cancel/replace, retries idempotentes).
- Controles operativos pre-trade/post-trade y circuit breakers.
- Runbooks, on-call y auditoría reforzada.

## 6) Secretos necesarios

### GitHub Secrets (obligatorio)

- `GOOGLE_SHEETS_RECOMMENDATIONS_CSV_URL`
- `SLACK_WEBHOOK_URL`
- `EMAIL_SMTP_USER`
- `EMAIL_SMTP_PASSWORD`
- `PAGES_DEPLOY_TOKEN` (si no se usa `GITHUB_TOKEN`)

### Solo para preparación broker real (no usar aún en paper)

- `BROKER_API_KEY`
- `BROKER_API_SECRET`
- `BROKER_BASE_URL`
- `BROKER_ACCOUNT_ID`

## 7) Política de versionado de datos

### Sí guardar en repo

- Señales, predicciones resumidas, decisiones de entrenamiento, reportes diarios, snapshots de dashboard.
- Archivos compactos/particionados con tamaño controlado.

### No guardar en repo

- Raw tick data masivo, barras históricas completas, features intermedias gigantes, modelos pesados binarios.
- Estos deben ir a storage externo/versionado (S3/GCS/DVC/LakeFS) con punteros en repo.

## 8) Política de particionado

- Partición primaria: `fecha (YYYY/MM/DD)`.
- Secundaria: `ticker`.
- Tercera: `horizonte (d1|w1|q1)`.
- Cuarta: `sesión/evento (OPEN, OPEN_PLUS_2H, OPEN_PLUS_4H, OPEN_PLUS_6H, CLOSE)`.

## 9) Criterios de éxito por fase

- **Fase 1:** pipeline reproducible, sin leakage, métricas base documentadas.
- **Fase 2:** estabilidad operativa + calibración dentro de thresholds por 4 semanas.
- **Fase 3:** paper trading continuo sin huecos de ejecución y con trazabilidad E2E.
- **Fase 4:** readiness checklist de broker real aprobada y kill-switch validado.

## 10) Estructura de visualización en GitHub Pages

- `site/index.html`: resumen ejecutivo y estado operativo.
- `site/data/dashboard.json`: KPIs principales (últimas predicciones, señales, salud del sistema).
- `site/data/metrics.json`: métricas técnicas/estadísticas.
- `site/data/strategy.json`: métricas de estrategia y curvas de equity.
- `site/assets/`: CSS/JS/images para dashboard estático.
