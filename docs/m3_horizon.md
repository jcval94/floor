# Horizonte `m3` (3 meses bursátiles)

## Objetivo
Incorporar un horizonte de predicción **`m3`** para estimar el mínimo probabilístico esperado en las próximas **13 semanas bursátiles relativas**, sin romper compatibilidad con `d1`, `w1` y `q1`.

## Definición funcional
- `m3` cubre las próximas **13 semanas bursátiles relativas** desde `as_of_date`.
- `floor_m3`: lower bound probabilístico del mínimo esperado dentro de todo el horizonte `m3`.
- `floor_week_m3`: índice **relativo** `1..13` de la semana donde es más probable observar el low más bajo del horizonte.
- La “semana con el valor más bajo” se define como la semana que contiene el **low mínimo observado** dentro de las 13 semanas.

## Justificación de diseño
- `w1` es demasiado corto para capturar drawdowns de régimen.
- `q1` (10 días hábiles) cubre ~2 semanas de mercado, no un trimestre operativo.
- `m3` agrega una escala intermedia-macro (13 semanas) más interpretable para riesgo táctico y asignación.
- Se mantiene separación semántica:
  - `q1`: ventana en días hábiles cortos.
  - `m3`: ventana semanal relativa de 3 meses bursátiles.

## Naming convention exacta
- Valor objetivo:
  - `floor_m3`
- Tiempo relativo:
  - `floor_week_m3` (entero en `[1, 13]`)
- Render humano / auditoría temporal:
  - `floor_week_m3_start_date` (`YYYY-MM-DD`)
  - `floor_week_m3_end_date` (`YYYY-MM-DD`)
  - `floor_week_m3_label` (ej. `Semana 04 (2026-04-06 → 2026-04-10)`)
  - `floor_week_m3_confidence` (`0..1`, probabilidad calibrada de ocurrencia en esa semana)

## Nuevas columnas por capa

### 1) Datasets de labels
Agregar:
- `floor_m3`
- `floor_week_m3`

Opcionales para auditoría offline (recomendado):
- `floor_week_m3_start_date`
- `floor_week_m3_end_date`

### 2) Outputs de forecast
Agregar:
- `floor_m3`
- `floor_week_m3`
- `floor_week_m3_start_date`
- `floor_week_m3_end_date`
- `floor_week_m3_label`
- `floor_week_m3_confidence`

### 3) Outputs de estrategia
Agregar al registro de decisión (si la estrategia consume `m3`):
- `floor_m3`
- `floor_week_m3`
- `floor_week_m3_confidence`
- `horizon = m3` en estrategias específicas, manteniendo coexistencia con `d1/w1/q1`.

### 4) Reportes
Agregar columnas/sections:
- `floor_m3`
- `floor_week_m3`
- `floor_week_m3_start_date`
- `floor_week_m3_end_date`
- `floor_week_m3_confidence`
- Campo narrativo recomendado: `m3_outlook` con interpretación resumida.

### 5) Dashboards
Agregar cards/tabla para `m3`:
- valor `floor_m3`
- semana relativa `floor_week_m3`
- rango de fechas (`start_date`, `end_date`)
- confianza (`floor_week_m3_confidence`)

## Compatibilidad hacia atrás
- No renombrar ni eliminar campos existentes de `d1`, `w1`, `q1`.
- Nuevos campos `m3` son **aditivos**.
- Readers antiguos deben tolerar columnas extra.
- Versionado sugerido de schema: incremento menor (`vX.Y+1`) por cambio backward-compatible.

## Reglas de renderizado (week index + fechas + etiqueta humana)
Dado `as_of_date`:
1. Construir las 13 semanas bursátiles relativas, cada una de lunes a viernes de mercado (saltando feriados).
2. `floor_week_m3 = k` selecciona la semana relativa `k`.
3. `floor_week_m3_start_date` = primer día bursátil de la semana `k`.
4. `floor_week_m3_end_date` = último día bursátil de la semana `k`.
5. `floor_week_m3_label` = `Semana {k:02d} ({start_date} → {end_date})`.
6. Si la semana contiene feriados y tiene menos sesiones, la etiqueta se mantiene con el rango real disponible.

## Contrato mínimo de forecast humano
El forecast humano debe mostrar, como mínimo:
- `floor_m3`
- `floor_week_m3`
- `floor_week_m3_start_date`
- `floor_week_m3_end_date`
- `floor_week_m3_confidence`

## Auditoría y versionado
- Registrar `as_of_date`, `model_version`, `data_version`, `horizon_config_version` en cada output que incluya `m3`.
- Persistir snapshots de forecast/report para trazabilidad.
- Mantener determinismo del cálculo de semana relativa con calendario de mercado versionado.
