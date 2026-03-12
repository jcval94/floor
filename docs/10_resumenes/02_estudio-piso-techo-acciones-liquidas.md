# Ficha de lectura — Estudio del piso y techo intradía en acciones líquidas

## Resumen ejecutivo (destilado)

- El piso/techo intradía se conoce con certeza **solo ex post**.
- En tiempo real, el problema útil se reformula como:
  1. predicción del mínimo/máximo en ventana futura,
  2. probabilidad de ruptura de extremos observados,
  3. detección de giros locales con validación estadística.
- Los indicadores OHLCV clásicos son baratos pero sensibles a régimen y ruido.
- Señales de microestructura/order flow suelen aportar más para horizontes cortos, a costa de datos más granulares y complejidad operativa.
- No hay método infalible: el valor está en un pipeline reproducible, validación temporal y evaluación económica realista.

## Definiciones reutilizables

- **Piso intradía global**: mínimo realizado de la sesión.
- **Techo intradía global**: máximo realizado de la sesión.
- **Evento operativo** (tiempo real):
  - probabilidad de que el precio actual sea (o quede cerca de) el mínimo/máximo del resto del día.
- **Piso/techo aproximado**: con tolerancia en ticks/bps/ATR para robustez frente a microvariaciones.

## Hallazgos metodológicos

### 1) Técnicos clásicos (OHLCV)
- VWAP y bandas alrededor de VWAP.
- RSI, MACD, Bollinger, ATR.
- Patrones de velas y niveles clásicos de soporte/resistencia.

**Pros**: simplicidad, bajo coste computacional, interpretabilidad.

**Contras**: lag, dependencia de régimen, sensibilidad a ruido y sobreajuste si no hay validación estricta.

### 2) Microestructura y order flow
- Spread, profundidad, imbalance, OFI, perfiles de volumen.
- Mayor poder explicativo en horizontes cortos bajo buena calidad de datos.

**Trade-off**: mejor señal potencial vs mayor exigencia de datos y control de latencia/sesgos.

### 3) Marcos estadísticos robustos
- Detección de cambios (CUSUM/segmentación).
- Extremos (EVT/POT).
- Volatilidad condicional (ARCH/GARCH).

Útiles para probabilizar extremos y construir umbrales adaptativos.

### 4) Machine Learning
- El rendimiento depende más de:
  - definición del evento,
  - ingeniería de variables,
  - validación temporal correcta,
que del algoritmo en sí.

## Checklist práctico para futuros proyectos

1. Definir target exacto (global, local, horizonte, tolerancia).
2. Seleccionar resolución temporal y fuente de precio (mid/last).
3. Construir baseline técnico simple.
4. Añadir features de microestructura cuando sea posible.
5. Validar con split temporal + purgado/embargo si hay solape de etiquetas.
6. Reportar métricas de clasificación + métricas económicas netas de costes.

## Prompt sugerido para reutilización

> “Diseña un pipeline para estimar probabilidad de piso/techo intradía con validación temporal estricta, baseline OHLCV, capa de microestructura (si hay datos L1/L2) y métricas estadísticas + económicas (incluyendo costes y slippage).”

