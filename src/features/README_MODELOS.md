# Competencia de modelos para piso/techo (d1, w1, q1)

Este módulo define **4 tipos de modelo por temporalidad** para competir bajo walk-forward y sin leakage.

## 1) EVT + Change-Point Hybrid (`evt_cp_*`) ✅ recomendado por los PDFs
- **Base metodológica:** los documentos del repo recomiendan combinar modelado de extremos (**EVT/POT**) con detección de cambios de régimen (**CUSUM/changepoints**).
- **Qué modela bien:** probabilidad de extremos de cola y timing de quiebres de régimen intradía.
- **Fortalezas:** alineado con literatura de piso/techo para extremos + confirmación de régimen.
- **Uso recomendado:** candidato principal “research-driven” para `floor_*` / `ceiling_*`.

## 2) XGBoost (`xgboost_*`)
- **Qué modela bien:** no linealidades e interacciones de features tabulares.
- **Fortalezas:** muy competitivo en datos financieros tabulares; manejo robusto de señales heterogéneas.
- **Uso recomendado:** benchmark moderno de alto desempeño.

## 3) LSTM Sequence (`lstm_*`)
- **Qué modela bien:** dinámica secuencial y dependencias temporales.
- **Fortalezas:** captura estructura temporal más allá de features estáticas.
- **Uso recomendado:** especialmente útil cuando la secuencia intradía aporta señal para `d1`.

## 4) Quantile Elastic Net (`qenet_*`)
- **Qué modela bien:** baseline lineal regularizado e interpretable.
- **Fortalezas:** estabilidad y explicabilidad para control de drift y sanity checks.
- **Uso recomendado:** baseline obligatorio para comparar complejidad vs robustez.

## Protocolo de competencia
- Mismo dataset, mismos folds walk-forward, misma purga/embargo.
- Métrica principal: **pinball loss** para cuantiles de piso/techo.
- Métricas secundarias: cobertura de intervalos, MAE de punto medio, hit-rate de `floor_breach` y `ceiling_reach`.
- Selección final por horizonte (`d1`, `w1`, `q1`) según desempeño out-of-sample.

## Retraining m3: hiperparametrización simple con CV (solo reentrenamiento)

En el pipeline operativo, la búsqueda de hiperparámetros **se ejecuta únicamente cuando el modo es `retrain`**.
Entrenamientos manuales, bootstrap/renewal y entrenamientos estándar mantienen configuración fija.

### Modelo de valor `m3_value_linear`
- Esquema: grid search + validación cruzada temporal (expanding window, 3 folds).
- Función objetivo CV: score compuesto de `pinball_loss + mae_realized_floor + |breach_rate-0.2| + calibration_error + (1-temporal_stability)`.
- Malla:
  - `atr_14`: `[-0.8, -0.6, -0.4]`
  - `trend_context_m3`: `[0.6, 0.8, 1.0]`
  - `drawdown_13w`: `[0.2, 0.4, 0.6]`
  - `dist_to_low_3m`: `[-0.7, -0.5, -0.3]`
  - `bias_offset`: `[-0.5, 0.0, 0.5]`

### Modelo de timing `m3_timing_multiclass`
- Esquema: grid search + validación cruzada temporal (expanding window, 3 folds).
- Función objetivo CV: score compuesto de `(1-top1_accuracy) + (1-top3_accuracy) + log_loss + brier_score + expected_week_distance/13 + calibration_error`.
- Malla:
  - `base`: `[1.6, 1.8]`
  - `distance_penalty`: `[0.20, 0.25, 0.30]`
  - `align_weight`: `[0.3, 0.4, 0.5]`
  - `recency_weight`: `[-0.02, -0.03, -0.04]`
  - `trend_weight`: `[0.1, 0.2, 0.3]`
