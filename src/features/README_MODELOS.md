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
