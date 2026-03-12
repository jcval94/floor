# Checklist: Investigación -> Paper Trading

## Datos y leakage
- [ ] Dataset de entrenamiento/versionado congelado.
- [ ] Tests de no leakage en verde (`tests/test_no_leakage.py`).
- [ ] Features y labels alineados temporalmente.

## Estrategia y riesgo
- [ ] Session gating validado (`tests/test_session_gating.py`).
- [ ] Límites de exposición y cash management definidos.
- [ ] Costes (commission/slippage/sell fees) explícitos en config.
- [ ] Criterios champion/challenger definidos.

## Ejecución simulada
- [ ] Paper executor validado end-to-end.
- [ ] Reconciliación señales/órdenes/fills en verde.
- [ ] Protección de idempotencia por ciclo validada.
- [ ] Reportes diarios/semanales/modelo habilitados.

## Operación
- [ ] Notificaciones por evento habilitadas (OPEN..CLOSE, drift, retrain, incident).
- [ ] Histórico versionado activo y sin duplicados.
- [ ] Export a Pages (latest + historical) funcionando.
- [ ] Runbook de incidentes documentado.
