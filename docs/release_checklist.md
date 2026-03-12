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

## 5) Respuesta a incidentes (obligatorio para PARTIAL/FAIL)
- [ ] RCA completado diferenciando síntoma vs causa raíz probable.
- [ ] Último `run_id` sano identificado en snapshots de workflow.
- [ ] Impacto evaluado por componente (forecasts, estrategias, paper trading, notificaciones).
- [ ] Fix inmediato aplicado o planificado con owner y ETA.
- [ ] Fix estructural registrado con pruebas de no regresión.
- [ ] Reporte de incidente generado en JSON auditable (`monitoring/incident_commander.py`).
