---
name: floor-experiment-execution
description: Enviar y seguir trabajos durables autorizados, cancelar y comprobar tracking y notificación.
---

# floor-experiment-execution

1. Consultar `floor_get_capabilities` y el contrato `../../tool_contracts.md`. No asumir disponibilidad por el nombre de esta guía. Usar solamente herramientas realmente descubiertas.
2. Seguir el flujo: floor_validate_experiment → floor_estimate_experiment_cost → floor_submit_experiment → floor_get_experiment_status.
3. Usar dataset IDs del catálogo y nombres de modelos/features registrados; nunca enviar rutas, SQL, shell o código generado. Mantener el mismo dataset, target, folds y condiciones al comparar. Revisar el plan exacto; no sustituir XGBoost por ExtraTrees ni champions por estimadores nuevos sin indicarlo y revisar la solicitud.
4. Rechazar leakage, temporalidad desconocida, ventanas incompatibles, recursos fuera de política o intentos de modificar estado oficial. Un hash no demuestra conocimiento histórico. Distinguir reconstrucción retrospectiva, OOS y prospectivo; exigir costos autoritativos cuando haya estrategia. Nunca habilitar PAPER/LIVE ni promoción.
5. Para ejecución registrada previamente autorizada usar `floor_submit_experiment` con una clave de idempotencia estable y conservar experiment_id/run_id. No reejecutar para obtener otra notificación. Para código nuevo/VIX/adapters ausentes devolver propuesta AWAITING_APPROVAL o NOT_AVAILABLE; preparar PR aislado, revisión de dependencias/seguridad y sandbox, sin ejecutar código arbitrario.
6. Consultar estado hasta terminal mediante reconexión; la continuidad depende del worker supervisado, no de mantener activa la conversación. Obtener `floor_get_experiment_results`, `floor_list_experiment_artifacts` y `floor_get_run_provenance`. No declarar ganador convincente solo por la mejor métrica ni confundir desviación de folds con intervalo de confianza.
7. Comprobar `floor_get_notification_status`: exigir DELIVERED y ts real de Slack. WAITING_CONFIG/PENDING/FAILED/UNKNOWN no son entregas. Reintentar solo fallos conocidos mediante `floor_resend_experiment_report`; conciliar UNKNOWN con el operador para no duplicar. No inventar enlaces públicos a reportes locales.

Esta guía no contiene lógica de negocio. La autoridad está en los contratos, servicios y pruebas de `src/ai_lab` y los módulos de dominio reutilizados. Es documentación versionada del repositorio; no instala una skill personal ni concede permisos.
