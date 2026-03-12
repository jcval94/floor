# Ficha de lectura — Identificar Pisos y Techos Bursátiles Intradía

## Objetivo del documento
Proponer un marco cuantitativo y de microestructura para detectar **pisos (soportes)** y **techos (resistencias)** intradía, evitando depender solo de heurísticas visuales de análisis técnico.

## Ideas clave (versión consumible para IA)

- El problema correcto no es “adivinar el mínimo/máximo absoluto” en tiempo real, sino estimar la **probabilidad condicional** de que el nivel actual actúe como extremo del resto de la sesión.
- El documento enfatiza que la detección de extremos intradía exige combinar:
  - teoría de microestructura,
  - dinámica del libro de órdenes,
  - modelado de volatilidad,
  - y aprendizaje supervisado con variables bien diseñadas.
- Se destaca la diferencia entre:
  - señales de reversión local (swing highs/lows),
  - vs. extremo global intradía (mínimo/máximo del día).

## Marco conceptual sugerido

1. **Microestructura primero**
   - El precio a corto plazo está mediado por liquidez, profundidad y flujo de órdenes.
   - Soporte/resistencia se interpreta como un fenómeno de absorción de presión compradora/vendedora.

2. **Variables predictivas (features)**
   - Flujo de órdenes (agresor comprador/vendedor).
   - Imbalance de libro.
   - Spread efectivo y profundidad por nivel.
   - Volatilidad intradía (rolling / condicional).
   - Distancia a niveles de referencia (máximo/mínimo parcial, VWAP, bandas, etc.).

3. **Etiquetado y objetivo supervisado**
   - Objetivos binarios/probabilísticos del tipo:
     - “¿se rompe el mínimo parcial en los próximos *h* minutos?”
     - “¿el precio actual está dentro de tolerancia de piso/techo futuro?”
   - Usar tolerancias en ticks/bps para evitar fragilidad por micro-ruido.

4. **Validación rigurosa**
   - Separación temporal estricta (sin fuga de información).
   - Métricas estadísticas + económicas (P&L simulado neto de costes).

## Limitaciones detectadas al extraer el PDF

- La extracción automática del texto de este PDF presenta ruido de codificación de fuentes en varios caracteres.
- Aun así, la estructura temática es clara y útil como base conceptual.

## Recomendación de uso futuro

Cuando pidas contenido nuevo basado en este material, usa este prompt base:

> “Trabaja sobre el marco de microestructura + aprendizaje supervisado para detectar pisos/techos intradía. Prioriza definición formal del evento, features de order flow, validación temporal y métricas económicas.”

