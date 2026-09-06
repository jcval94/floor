# Robust range v3

## Resultado

`robust_range_v3` supera al champion `boosted_stumps` anterior en los tres
errores exigidos —floor, ceiling y spread— para `d1`, `w1` y `q1`. La mejora se
repite en validacion y en un test cronologico ciego. El test no se uso para
elegir arquitectura, hiperparametros ni peso del ensemble.

### Validacion usada para seleccion

| Horizonte | Floor MAE pct (base -> v3) | Mejora | Ceiling MAE pct (base -> v3) | Mejora | Spread MAE pct (base -> v3) | Mejora | Cobertura conjunta delta |
|---|---:|---:|---:|---:|---:|---:|---:|
| D1 | 0.011814 -> 0.011678 | 1.16% | 0.012485 -> 0.012197 | 2.31% | 0.012019 -> 0.011349 | 5.58% | -3.97 pp |
| W1 | 0.022810 -> 0.022615 | 0.85% | 0.029328 -> 0.028376 | 3.25% | 0.027638 -> 0.026200 | 5.20% | -2.91 pp |
| Q1 | 0.030072 -> 0.029745 | 1.09% | 0.045543 -> 0.043883 | 3.64% | 0.040443 -> 0.038443 | 4.94% | -3.75 pp |

### Test ciego posterior a la seleccion

| Horizonte | Floor MAE pct (base -> v3) | Mejora | Ceiling MAE pct (base -> v3) | Mejora | Spread MAE pct (base -> v3) | Mejora | Cobertura conjunta delta |
|---|---:|---:|---:|---:|---:|---:|---:|
| D1 | 0.012573 -> 0.012340 | 1.85% | 0.013364 -> 0.013138 | 1.69% | 0.013041 -> 0.012168 | 6.70% | -3.09 pp |
| W1 | 0.025785 -> 0.025209 | 2.24% | 0.029382 -> 0.028698 | 2.33% | 0.030357 -> 0.028345 | 6.63% | -3.52 pp |
| Q1 | 0.035227 -> 0.034282 | 2.68% | 0.040486 -> 0.039360 | 2.78% | 0.041130 -> 0.037869 | 7.93% | -3.21 pp |

La cobertura conjunta es una metrica de control, no el objetivo optimizado. La
promocion admite como maximo una regresion absoluta de cinco puntos
porcentuales; v3 queda dentro del limite en todos los horizontes. Estos rangos
no deben interpretarse como intervalos probabilisticos calibrados.

## Modelo

Cada boundary usa un ensemble conservador con 80% del modelo
`boosted_stumps` reentrenado sobre el mismo train y 20% de un nuevo head:

- floor: mediana de la excursion bajista observada expresada en unidades de
  ATR, una estimacion robusta y optima para error absoluto;
- ceiling: `HistGradientBoostingRegressor` poco profundo, con perdida absoluta,
  49 features point-in-time y regularizacion fija;
- serving: los arboles del ceiling se exportan a JSON y se ejecutan con un
  predictor Python propio. `scikit-learn` solo es una dependencia de training.

El peso 20% fue el mayor peso fijo elegido en validacion que mantuvo la
regresion de cobertura dentro del guardrail. Se congelo antes de abrir el test
ciego.

## Contrato temporal y datos

- Fuente: Yahoo Finance, 50 acciones del universo y SPY como benchmark.
- Ventana descargada: 5 anos, 62.750 filas modelables y 1.255 sesiones por
  simbolo antes de construir labels elegibles.
- Train: 2021-09-07 a 2026-01-29, 1.104 sesiones.
- Validacion: 2026-01-30 a 2026-05-18, 75 sesiones.
- Test ciego: 2026-05-19 a 2026-09-04, 76 sesiones.
- Filas elegibles de test: 3.750 (`d1`), 3.550 (`w1`) y 3.300 (`q1`).
- La elegibilidad se purga por horizonte para que el final del target nunca
  atraviese el limite de su split.

## Gate de promocion

El gate `classic-boundary-pareto-v2` solo promueve un candidato cuando, sobre
la misma validacion actual:

1. `mae_floor_pct` es estrictamente menor;
2. `mae_ceiling_pct` es estrictamente menor;
3. `mae_spread_pct` es estrictamente menor;
4. la cobertura conjunta no cae mas de cinco puntos porcentuales.

La accion `robust_range_v3` reconstruye el dataset desde cero, entrena solo el
challenger, ejecuta el gate, abre una unica vez el test reservado, verifica
paridad train/serve y publica los champions y el informe de auditoria como
artefacto de GitHub Actions.

## Reproduccion

```bash
python -m pip install -e '.[dev]'
make init-dbs
PYTHONPATH=src python -m storage.yahoo_ingest \
  --db data/market/market_data.sqlite --range 5y --interval 1d
PYTHONPATH=src python -m features.build_training_from_db \
  --db data/market/market_data.sqlite \
  --output data/training/yahoo_market_rows.jsonl
PYTHONPATH=src python -m features.run_features \
  --input data/training/yahoo_market_rows.jsonl \
  --output data/training/modelable_dataset.json \
  --validation-days 75 --test-days 76
PYTHONPATH=src python -m models.train_classic_horizons \
  --dataset data/training/modelable_dataset.json \
  --output-dir artifacts/candidate --version robust-range-v3 \
  --tasks d1,w1,q1 --training-mode manual --families robust_range_v3
```

El horizonte `m3` tiene un contrato distinto: predice floor y semana de timing,
pero no define ceiling. Por eso este cambio cubre todos los contratos del repo
que si producen simultaneamente floor y ceiling: `d1`, `w1` y `q1`.
