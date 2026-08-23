# site/data

Este directorio contiene **artefactos estáticos generados** consumidos por el dashboard de GitHub Pages.

## Fuente autoritativa
- Los `*.json` **no se versionan en Git**.
- Se generan durante `.github/workflows/pages.yml` mediante `utils.pages_publish`.
- La publicación oficial usa **GitHub Actions / deploy-pages**; `docs/` no es una fuente de datos de producción.
- Cada deploy genera `audit.json` y `publication_manifest.json` con el commit y hashes publicados.
- Si el batch, la frescura, el contrato o los champions no pasan la auditoría, el sitio se despliega en estado `BLOCKED` con forecasts accionables suprimidos.

## Contrato
- Formato principal: `*.json`.
- No depende de APIs client-side externas.
- No incluir secretos ni credenciales.
- Los forecasts publicables deben pertenecer a un único batch global y a una única versión del suite.

## Archivos generados esperados
- `audit.json`: evidencia de publicación, blockers/warnings y trazabilidad.
- `dashboard.json`: overview del sistema.
- `forecasts.json`: forecasts auditados; `rows=[]` cuando la publicación está bloqueada.
- `universe.json`: universo de tickers.
- `opportunities.json`: ranking descriptivo generado durante el build.
- `strategy.json`: equity/drawdown y estado estrategia.
- `metrics.json`: métricas públicas/model health.
- `models.json`: champions, compatibilidad y timeline.
- `drift.json`: semáforo y decisión de retraining.
- `incidents.json`: incidente más reciente y su impacto.

Nunca usar un JSON histórico versionado como fallback de producción.
