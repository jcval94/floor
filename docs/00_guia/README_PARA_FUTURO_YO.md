# README para mi yo del futuro (cómo aprovechar este conocimiento)

## Objetivo
Usar estos materiales para crear contenido, prompts, diseño experimental y pipelines de modelado sobre **pisos/techos intradía** sin volver a leer los PDFs completos cada vez.

## Flujo de trabajo recomendado

1. **Contexto rápido (5 min):**
   - Leer `docs/10_resumenes/02_estudio-piso-techo-acciones-liquidas.md`.
   - Luego `docs/10_resumenes/01_identificar-pisos-techos-intradia.md`.

2. **Definir la tarea exacta:**
   - ¿Necesitas explicación conceptual?
   - ¿Diseño de features?
   - ¿Plan de backtesting?
   - ¿Prompt para un modelo?

3. **Bajar a evidencia textual:**
   - Buscar frases o secciones en `docs/20_fuentes/*.txt`.
   - Priorizar el archivo con mejor calidad textual (`estudio-...txt`) para citas textuales.

4. **Producir salida estructurada:**
   - Siempre entregar: definición del evento, supuestos, datos requeridos, validación temporal y métricas económicas.

## Plantillas de uso (copiar/pegar)

### A) Para pedir diseño de pipeline
> Diseña un pipeline para estimar probabilidad de piso/techo intradía en ventana H. Incluye:
> 1) definición formal del target con tolerancia,
> 2) baseline OHLCV,
> 3) capa de microestructura (si hay L1/L2),
> 4) validación temporal sin leakage,
> 5) métricas estadísticas y económicas netas de costes.

### B) Para crear documento técnico
> Redacta una nota técnica en 6 secciones: problema, formalización, variables, modelos, validación y riesgos de implementación. Señala explícitamente límites del enfoque y condiciones de fallo.

### C) Para checklist de implementación
> Crea checklist ejecutable (datos, features, etiquetas, split temporal, calibración, backtest con slippage/fees, monitoreo en producción).

## Calidad de fuentes (muy importante)

- `docs/20_fuentes/estudio-del-piso-y-el-techo-intradia-en-acciones-liquidas-definiciones-literatura-metodos-de.txt`:
  - Calidad de texto **alta**.
- `docs/20_fuentes/identificar-pisos-y-techos-bursatiles-intradia.txt`:
  - Calidad **media/baja** por codificación de fuente PDF.
  - Usarlo para ideas generales, no para citas textuales sensibles.

## Convención operativa para futuras tareas

- Primero resumen (`10_resumenes`), después fuente (`20_fuentes`).
- Evitar afirmar “método infalible”.
- Siempre incluir advertencias de régimen, latencia, costos y sesgo de selección.

