# Revisión quincenal de modelos (ML lead + risk owner)

## Diagnóstico ejecutivo
El campeón (champion-v0) no muestra evidencia de drift material en la telemetría disponible, pero la ventana observada es insuficiente (solo actividad del 2026-03-12) y faltan métricas públicas de performance/estrategia. Riesgo operativo medio por baja observabilidad y cobertura temporal incompleta.

## Diagnóstico técnico
- Drift de features: No concluyente por ausencia de dataset de features histórico quincenal.
- Drift de targets: No concluyente por ausencia de labels realizados en ventana quincenal.
- Drift de cobertura/calibración: No concluyente para calibración; faltan series en site/data/metrics.json.
- Deterioro de pinball loss: No medible; falta serie de evaluación out-of-sample.
- Deterioro de breach rate: No medible; falta serie de breach auditada.
- Deterioro de métricas de estrategia: No medible; status=no_strategy_report.
- Cambios de schema: Sin evidencia de ruptura de esquema en artifacts de predicción/señales.
- Cobertura/calidad de datos recientes: Cobertura temporal insuficiente para revisión quincenal (1 día efectivo).
- Estabilidad del campeón vigente: Modelo único champion-v0 consistente en predicciones, pero sin evidencia robusta de estabilidad estadística.

## Recomendación
- Decisión: **RETRAIN_SOON**
- Estado: **WARN**
- Nivel de drift agregado: **YELLOW**

## Thresholds disparados
- review_window_days_min: observado=1 | umbral=14 | severidad=YELLOW
- public_metrics_status: observado=no_public_metrics | umbral=available | severidad=YELLOW
- strategy_report_status: observado=no_strategy_report | umbral=available | severidad=YELLOW

## Archivos a actualizar
- data/training/reviews.jsonl
- data/reports/retraining_review_2026-03-12.json
- docs/release_checklist.md

## Plan exacto de ejecución (RETRAIN_SOON)
- 1) Completar backfill de 14 días de features/labels y validar cobertura por columna >= 90%.
- 2) Recalcular pinball loss y breach rate por horizonte (d1, w1, q1) contra baseline champion-v0.
- 3) Ejecutar competencia de modelos y seleccionar candidato si mejora pinball >= 2% y breach no empeora.
- 4) Correr paper-trading A/B por 10 sesiones con límites de riesgo vigentes.
- 5) Promover modelo con acta Go/No-Go y actualizar config/model_version + changelog.

## Siguiente revisión
- Fecha: 2026-03-26
- Condición: Adelantar a RETRAIN_NOW si aparece degradación material: pinball +0.05, breach +0.06, caída Sharpe >=0.50 o ruptura de schema.

## JSON final
```json
{
  "status": "WARN",
  "decision": "RETRAIN_SOON",
  "drift_level": "YELLOW",
  "reasons": [
    "El campeón (champion-v0) no muestra evidencia de drift material en la telemetría disponible, pero la ventana observada es insuficiente (solo actividad del 2026-03-12) y faltan métricas públicas de performance/estrategia. Riesgo operativo medio por baja observabilidad y cobertura temporal incompleta.",
    "review_window_days_min=1",
    "public_metrics_status=no_public_metrics",
    "strategy_report_status=no_strategy_report"
  ],
  "metrics": {
    "prediction_rows": 60,
    "signal_rows": 60,
    "symbols_covered": [
      "AAPL",
      "MSFT",
      "NVDA",
      "SPY"
    ],
    "horizon_counts": {
      "d1": 20,
      "w1": 20,
      "q1": 20
    },
    "model_versions": {
      "champion-v0": 60
    },
    "actions": {
      "HOLD": 60
    },
    "confidence_min": 0.5,
    "confidence_max": 0.5,
    "as_of_min": "2026-03-12T13:48:48.667846-04:00",
    "as_of_max": "2026-03-12T17:54:43.316831-04:00",
    "metrics_status": "no_public_metrics",
    "strategy_status": "no_strategy_report"
  },
  "models_affected": [
    "champion-v0"
  ],
  "files_updated": [
    "data/reports/retraining_review_2026-03-12.json",
    "data/reports/retraining_review_2026-03-12.md",
    "data/training/reviews.jsonl"
  ]
}
```
