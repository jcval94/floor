# docs/data

Este directorio **no es una fuente de producción de GitHub Pages**.

Los payloads `*.json` se excluyen deliberadamente de Git para impedir que un mirror histórico de `docs/` pueda mostrar forecasts obsoletos si la configuración de Pages cambia accidentalmente a “Deploy from branch”.

La única publicación autoritativa es `.github/workflows/pages.yml` mediante GitHub Actions / `deploy-pages`, que genera y audita `site/data/*.json` en cada deploy.

Si este directorio aparece sin JSON, es el comportamiento esperado y seguro.
