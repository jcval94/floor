# Cierre operativo 2026-03-12

Estado: **PARTIAL** | Integridad: **WARN**

## Resumen ejecutivo
- Señales reconciliadas: 54
- Órdenes reconciliadas: 0
- Fills reconciliados: 0
- Snapshots de portfolio: 0
- Persistencia histórica base (predictions/signals/workflow snapshots): OK

## Tabla por estrategia (actividad/PnL provisional)
| Estrategia | Señales | Órdenes | Fills | PnL | Estado |
|---|---:|---:|---:|---:|---|
| consensus | 0 | 0 | 0 | 0.00 | sin_datos_estrategia |
| ai_only | 0 | 0 | 0 | 0.00 | sin_datos_estrategia |
| model_only | 0 | 0 | 0 | 0.00 | sin_datos_estrategia |
| mean_reversion_floor_w1 | 0 | 0 | 0 | 0.00 | sin_datos_estrategia |
| breakout_protected_by_floor | 0 | 0 | 0 | 0.00 | sin_datos_estrategia |

## Incidentes
| ID | Severidad | Área | Descripción |
|---|---|---|---|
| INC-ORD-001 | high | execution | No se encontraron órdenes en data/orders; no hubo ejecución operativa. |
| INC-FILL-001 | high | execution | No se encontraron fills/trades en data/trades; PnL realizado no disponible. |
| INC-PORT-001 | high | risk | No hay portfolio snapshots persistidos en data/metrics. |
| INC-SIG-001 | medium | strategy | Todas las señales del día quedaron en HOLD (54/54); posible degradación de sensibilidad o gating excesivo. |
| INC-WF-001 | medium | orchestration | No existe snapshot intraday OPEN en workflow_runs para el día analizado. |

## Acciones correctivas mañana
1. Habilitar persistencia de órdenes/fills/snapshots por ciclo y verificar volumen no nulo.
2. Publicar reconciliación diaria automática con semáforo PASS/WARN/FAIL.
3. Revisar threshold de señales para reducir HOLD-rate extremo manteniendo guardrails.
4. Completar cobertura de snapshots intradía (OPEN, OPEN+2H, OPEN+4H, OPEN+6H, CLOSE).
