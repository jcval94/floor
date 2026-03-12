# Checklist: Paper Trading -> Live Trading (Futuro)

## Performance y robustez
- [ ] Track record en paper suficiente (periodo mínimo acordado).
- [ ] Métricas objetivo cumplidas (drawdown, Sharpe/Sortino, turnover, capacity).
- [ ] Drift/retrain policy estable y auditada.
- [ ] Incidentes críticos en paper: 0 en ventana de validación.

## Riesgo y cumplimiento
- [ ] Límites de riesgo hard-enforced en runtime.
- [ ] Kill-switch manual y automático probado.
- [ ] Revisión de secretos/permisos y segregación de credenciales.
- [ ] Logging/auditoría y retención de evidencias acordadas.

## Ejecución y broker adapter
- [ ] Especificación de adapter a broker real definida.
- [ ] Simulación vs broker sandbox comparada con reconciliación.
- [ ] Validación de estados de orden/fill parcial/cancelación en broker.
- [ ] Procedimiento de fallback a paper definido.

## Operación de release live
- [ ] Dry-run operativo completo aprobado.
- [ ] On-call y canales de incidentes confirmados.
- [ ] Checklist de release interna completada.
- [ ] Go/No-Go final con responsables.
