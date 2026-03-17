# Experimentos

Esta carpeta contiene experimentos operativos que se ejecutan **solo en workflows manuales/temporales**.

## Experimento 01: Auditoría de tablas SQLite

Script:

```bash
python experimento/db_table_audit.py --db data/persistence/app.sqlite --out-dir experimento/artifacts/db_audit --head 5
```

Salida:

- Un CSV por tabla con `head(5)`.
- `summary.json` y `summary.md` con estado de frescura/volumen por tabla.
