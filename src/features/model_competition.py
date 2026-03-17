"""Model competition design for floor/ceiling forecasting.

This module defines model families that compete for each horizon (d1, w1, q1):
1) EVT + Change-Point Hybrid (recommended by repo PDFs for intraday extremes/regimes)
2) XGBoost (strong tabular non-linear baseline)
3) LSTM Sequence Model (temporal sequence representation)
4) Quantile Elastic Net (interpretable linear baseline)
"""

from __future__ import annotations

from dataclasses import dataclass


HORIZONS = ("d1", "w1", "q1")


@dataclass(frozen=True)
class ModelSpec:
    model_id: str
    model_family: str
    horizon: str
    predicts: tuple[str, ...]
    objective: str
    notes: str


def build_model_specs() -> list[ModelSpec]:
    specs: list[ModelSpec] = []
    for horizon in HORIZONS:
        specs.extend(
            [
                ModelSpec(
                    model_id=f"evt_cp_{horizon}",
                    model_family="evt_changepoint_hybrid",
                    horizon=horizon,
                    predicts=(f"floor_{horizon}", f"ceiling_{horizon}"),
                    objective="tail_extreme_probabilistic_regression",
                    notes=(
                        "Modelo basado en recomendaciones de los estudios del repo: "
                        "EVT/POT para colas + detector de régimen (CUSUM/changepoints) "
                        "para robustecer timing de extremos piso/techo."
                    ),
                ),
                ModelSpec(
                    model_id=f"xgboost_{horizon}",
                    model_family="xgboost",
                    horizon=horizon,
                    predicts=(f"floor_{horizon}", f"ceiling_{horizon}"),
                    objective="quantile_regression",
                    notes="Ensamble boosting eficiente para datos tabulares con no linealidades e interacciones.",
                ),
                ModelSpec(
                    model_id=f"lstm_{horizon}",
                    model_family="lstm_sequence",
                    horizon=horizon,
                    predicts=(f"floor_{horizon}", f"ceiling_{horizon}"),
                    objective="multi_output_regression",
                    notes="Modelo secuencial para dependencia temporal; usa ventanas lookback y masking por missing.",
                ),
                ModelSpec(
                    model_id=f"qenet_{horizon}",
                    model_family="quantile_elastic_net",
                    horizon=horizon,
                    predicts=(f"floor_{horizon}", f"ceiling_{horizon}"),
                    objective="quantile_regression",
                    notes="Baseline interpretable con regularización; útil para estabilidad y explainability.",
                ),
            ]
        )
    return specs


def competition_protocol() -> dict:
    """Walk-forward competition protocol shared by all horizons."""

    return {
        "selection_metric": {
            "primary": "pinball_loss_p10_p90",
            "secondary": ["coverage_10_90", "mae_midpoint", "hit_rate_floor_breach", "hit_rate_ceiling_reach"],
        },
        "validation": {
            "scheme": "walk_forward",
            "retrain_each_fold": True,
            "purge_gap_days": 1,
            "embargo_days": 1,
        },
        "pdf_recommendation_traceability": {
            "recommended_core": ["EVT/POT", "CUSUM/ChangePoints", "Temporal CV with purge/embargo"],
            "source_docs": [
                "docs/10_resumenes/02_estudio-piso-techo-acciones-liquidas.md",
                "docs/20_fuentes/estudio-del-piso-y-el-techo-intradia-en-acciones-liquidas-definiciones-literatura-metodos-de.txt",
            ],
        },
        "tie_break": "best_calibration_then_lowest_turnover_error",
    }


def build_model_competition_plan() -> dict:
    specs = build_model_specs()
    by_horizon: dict[str, list[dict]] = {h: [] for h in HORIZONS}
    models: list[dict] = []
    for spec in specs:
        payload = spec.__dict__
        by_horizon[spec.horizon].append(payload)
        models.append(payload)
    return {
        "models": models,
        "models_by_horizon": by_horizon,
        "protocol": competition_protocol(),
    }
