# site/data

Este directorio contiene **artefactos estáticos** consumidos por el dashboard de GitHub Pages.

## Contrato
- Formato principal: `*.json`.
- Fuente: generación offline desde workflows (`utils.pages_build`).
- No depende de APIs client-side externas.
- No incluir secretos ni credenciales.

## Archivos esperados
- `dashboard.json`: overview del sistema.
- `forecasts.json`: forecasts y oportunidades.
- `universe.json`: universo inicial (50 tickers).
- `opportunities.json`: ranking por spread esperado.
- `strategy.json`: equity/drawdown y estado estrategia.
- `metrics.json`: métricas públicas/model health.
- `models.json`: campeón actual + timeline.
- `drift.json`: semáforo y decisión de retraining.
- `incidents.json`: incidente más reciente y su impacto.

## Actualización
Se actualiza automáticamente por GitHub Actions y se publica en `gh-pages` como project site.
