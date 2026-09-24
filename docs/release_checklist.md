# Release Checklist

## Calidad técnica

- [ ] `ruff check src tests scripts` limpio.
- [ ] `mypy --ignore-missing-imports src` limpio.
- [ ] `pytest -q` en verde.
- [ ] Cobertura mínima de CI satisfecha.
- [ ] `python scripts/validate_repo.py` en verde.
- [ ] `PYTHONPATH=src python -m utils.resource_ownership` en verde.

## Datos y modelos

- [ ] Champions de serving pasan `utils.model_artifact_guard`.
- [ ] Solo `retrain_execute` modifica `data/training/models/*.json`.
- [ ] Review y champion versions son coherentes; si divergen, Pages marca `STALE_REVIEW` y sirve artifact truth.
- [ ] Forecast batch cumple contrato multi-horizonte y frescura.
- [ ] No se versionaron SQLite, JSONL runtime ni `site/data/*.json`.

## Estado operacional

- [ ] Runtime restore/publish usa generación inmutable y checksum.
- [ ] CAS parent validation está activa para runtime writers.
- [ ] Checkpoint frontier es monotónico.
- [ ] Monitoring no escribe runtime-state.
- [ ] Research no escribe runtime-state salvo workflows explícitamente autorizados por contrato.
- [ ] History rewrite, si aplica, es manual y usa exact `--force-with-lease` + `--atomic`.

## Pages

- [ ] Build fija runtime/research/monitoring antes de restaurar.
- [ ] Audit registra `source_commit` y `state_snapshot`.
- [ ] Forecasts bloqueados no exponen rows/opportunities accionables.
- [ ] Static security/CSP pasa.
- [ ] Deployment autoritativo proviene de `site/`.

## Scheduler

- [ ] Intraday/EOD no se ejecutan por cada push a main.
- [ ] Watchdog solo despacha cuando no existe run reciente/activo.
- [ ] Monitoring workflow-run backstop exige evidencia runtime real.

## Seguridad y administración

- [ ] No hay secretos hardcodeados.
- [ ] Dependabot está habilitado para pip y GitHub Actions.
- [ ] GitHub Pages Source está configurado como **GitHub Actions**.
- [ ] `main` tiene ruleset/branch protection con PR y CI requerido.

Los dos últimos controles son settings administrativos del repositorio; no pueden configurarse desde un workflow con el `GITHUB_TOKEN` estándar.

## Post-release

- [ ] CI de `main` termina en success.
- [ ] Primer Pages deployment post-merge termina en success.
- [ ] Si cambió persistencia, confirmar restore + publish real.
- [ ] Si cambió scheduling, comprobar que no aparecen duplicados gate-only.
- [ ] Si hubo incidente, generar evidencia/RCA auditable antes de cerrar.
