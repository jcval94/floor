# Índice de conocimiento (ordenado)

Este repositorio quedó organizado para consumo rápido por humanos y por asistentes.

## Estructura recomendada

- `docs/00_guia/`
  - Guías de uso y estrategia de consumo.
- `docs/10_resumenes/`
  - Resúmenes curados de alto valor semántico (mejor punto de entrada).
- `docs/20_fuentes/`
  - Extracciones de texto plano desde los PDFs originales (útiles para búsqueda/RAG).

## Lectura sugerida (orden)

1. `docs/00_guia/README_PARA_FUTURO_YO.md`
2. `docs/10_resumenes/02_estudio-piso-techo-acciones-liquidas.md`
3. `docs/10_resumenes/01_identificar-pisos-techos-intradia.md`
4. `docs/20_fuentes/*.txt` para citas o recuperación de texto

## Regeneración de fuentes

```bash
python scripts/extract_pdf_text.py
```

Genera/actualiza:

- `docs/20_fuentes/*.txt`
- `docs/20_fuentes/manifest.json`

