# Release Checklist (Interna)

## 1) Calidad técnica
- [ ] `ruff check src tests scripts` limpio.
- [ ] `mypy --ignore-missing-imports src` limpio.
- [ ] `pytest -q` en verde.
- [ ] Cobertura mínima razonable (>=60%) validada en CI.
- [ ] Smoke test end-to-end ejecutado (`python scripts/validate_repo.py --smoke`).

## 2) Datos y configuración
- [ ] Configs en `config/*.yaml` revisados y válidos.
- [ ] Schemas de datasets de `site/data/*.json` validados.
- [ ] No hay cambios no intencionados en snapshots históricos.
- [ ] Se generó snapshot `latest` y snapshot histórico particionado.

## 3) Seguridad y operaciones
- [ ] Revisión de secretos y permisos completada.
- [ ] Ningún secreto hardcodeado en código/config/data público.
- [ ] Alertas y notificaciones con canal principal verificado.
- [ ] Canal secundario opcional probado (si aplica).

## 4) Gobernanza de release
- [ ] Changelog/PR description actualizado.
- [ ] Validación funcional por dueño de estrategia.
- [ ] Rollback plan definido.
- [ ] Aprobaciones internas completas.
