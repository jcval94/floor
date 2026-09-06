"""Model competition design for floor/ceiling forecasting.

The names in this module describe the algorithms that are actually implemented.
They intentionally avoid claiming XGBoost/LSTM/EVT when the current code uses
simpler in-repo baselines.
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
                    model_id=f"robust_range_v3_{horizon}",
                    model_family="robust_range_v3",
                    horizon=horizon,
                    predicts=(f"floor_{horizon}", f"ceiling_{horizon}"),
                    objective="robust_hybrid_median_absolute_error",
                    notes=(
                        "Ensemble conservador: 80% boosted-stumps como ancla y 20% challenger. "
                        "El challenger usa floor estructural ATR-mediana y ceiling mediante "
                        "HistGradientBoosting poco profundo con perdida absoluta. Los arboles "
                        "se exportan a JSON y se sirven sin sklearn."
                    ),
                ),
                ModelSpec(
                    model_id=f"regime_median_{horizon}",
                    model_family="regime_median",
                    horizon=horizon,
                    predicts=(f"floor_{horizon}", f"ceiling_{horizon}"),
                    objective="regime_conditioned_median_regression",
                    notes=(
                        "Baseline por terciles de volatilidad y signo de tendencia; "
                        "usa medianas observadas por régimen. No implementa EVT/POT ni changepoints."
                    ),
                ),
                ModelSpec(
                    model_id=f"boosted_stumps_{horizon}",
                    model_family="boosted_stumps",
                    horizon=horizon,
                    predicts=(f"floor_{horizon}", f"ceiling_{horizon}"),
                    objective="squared_error_boosted_stumps",
                    notes=(
                        "Boosting ligero implementado en el repo mediante decision stumps; "
                        "no depende de XGBoost."
                    ),
                ),
                ModelSpec(
                    model_id=f"sequence_linear_{horizon}",
                    model_family="sequence_linear",
                    horizon=horizon,
                    predicts=(f"floor_{horizon}", f"ceiling_{horizon}"),
                    objective="regularized_linear_regression",
                    notes=(
                        "Baseline lineal regularizado sobre variables de contexto temporal; "
                        "no implementa una red LSTM."
                    ),
                ),
                ModelSpec(
                    model_id=f"regularized_linear_{horizon}",
                    model_family="regularized_linear",
                    horizon=horizon,
                    predicts=(f"floor_{horizon}", f"ceiling_{horizon}"),
                    objective="regularized_linear_regression",
                    notes=(
                        "Baseline lineal regularizado interpretable. El objetivo actual es error cuadrático, "
                        "no una regresión cuantílica/Elastic Net completa."
                    ),
                ),
            ]
        )
    return specs


def competition_protocol() -> dict:
    """Protocol contract for classic horizon competition.

    The implemented trainer requires a dedicated chronological validation split.
    It must never use the test split or a slice of training for champion selection.
    """

    return {
        "selection_metric": {
            "primary": "mae_spread",
            "secondary": [
                "mae_floor",
                "mae_ceiling",
                "test_interval_coverage",
                "empirical_breach_rate",
            ],
        },
        "validation": {
            "scheme": "chronological_validation_holdout",
            "test_used_for_selection": False,
            "training_fallback_allowed": False,
            "purge_contract": "split_eligible_<horizon> / target_end_date_<horizon>",
        },
        "implementation_traceability": {
            "robust_range_v3": (
                "80/20 anchored ensemble: boosted-stumps + ATR-normalized median "
                "floor / serialized shallow histogram gradient booster ceiling"
            ),
            "regime_median": "volatility terciles + trend sign + observed median",
            "boosted_stumps": "in-repo additive decision stumps",
            "sequence_linear": "L2-regularized linear baseline on temporal-context features",
            "regularized_linear": "L2-regularized linear baseline",
        },
        "tie_break": "lowest_total_boundary_mae",
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
