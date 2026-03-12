# Revisión de pestañas (`forecasts.html` y navegación principal)

Fuente revisada: `https://jcval94.github.io/floor/forecasts.html` y sus pestañas de navegación superiores.

## 1) Home
- **Estado observado:** carga correctamente, muestra tarjetas de System, Drift e Incidents.
- **Qué debería cambiar:** no se detectaron fallas funcionales en la navegación o render principal.

## 2) Forecasts
- **Estado observado:** carga correctamente, renderiza tarjetas por ticker y tabla de oportunidades.
- **Mejora aplicada:** se añadió estado vacío para tarjetas cuando no haya pronósticos, con mensaje explícito.
- **Mejora aplicada:** se añadió estado vacío para la tabla “Top oportunidades” cuando no existan filas.

## 3) Tickers
- **Estado observado:** carga correctamente y permite ir a detalle por ticker.
- **Mejora aplicada:** cuando un ticker no tenga filas en el dataset, ahora se muestra mensaje claro (“Ticker sin pronóstico disponible...”) en lugar de una tarjeta vacía.

## 4) Strategies
- **Estado observado:** carga correctamente; se renderizan curvas si existe información.
- **Qué debería cambiar:** no se detectaron fallas funcionales obligatorias.

## 5) Models
- **Estado observado:** carga correctamente.
- **Falla potencial detectada:** el bloque de calibración estaba truncando el JSON de `health` a 220 caracteres, ocultando información útil.
- **Mejora aplicada:** ahora se muestra el JSON completo y formateado dentro de `<pre>` para facilitar lectura.
- **Mejora aplicada:** se añadió estado vacío para timeline cuando no existan eventos.

## 6) Drift & Retraining
- **Estado observado:** carga correctamente.
- **Mejora aplicada:** se añadió estado vacío para la tabla de umbrales cuando `thresholds` llegue vacío.

## 7) Incidents
- **Estado observado:** carga correctamente.
- **Mejora aplicada:** se añadió estado vacío para tabla de impacto cuando no haya pares clave/valor.

## 8) About
- **Estado observado:** carga correctamente.
- **Qué debería cambiar:** no se detectaron fallas funcionales.

## Resumen de hallazgos
- No se detectaron errores de consola ni errores de runtime durante la revisión de pestañas en la versión publicada.
- Se aplicaron mejoras de robustez de UI para estados vacíos y de legibilidad en Models (calibración completa).
- Se sincronizaron los cambios tanto en `docs/assets/app.js` como en `site/assets/app.js`.
