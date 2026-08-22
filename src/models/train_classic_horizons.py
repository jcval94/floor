from __future__ import annotations

import argparse
import csv
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from features.model_competition import HORIZONS, build_model_specs
from models.horizon_timing import fit_horizon_timing

logger = logging.getLogger(__name__)

HORIZON_TARGETS = {
    "d1": ("floor_d1", "ceiling_d1"),
    "w1": ("floor_w1", "ceiling_w1"),
    "q1": ("floor_q1", "ceiling_q1"),
}

FEATURES_BY_FAMILY: dict[str, tuple[str, ...]] = {
    "qenet": (
        "atr_14",
        "trend_context_m3",
        "drawdown_13w",
        "dist_to_low_3m",
        "ai_horizon_alignment",
    ),
    "xgboost": (
        "atr_14",
        "trend_context_m3",
        "drawdown_13w",
        "dist_to_low_3m",
        "ai_horizon_alignment",
        "rel_strength_20",
    ),
    "lstm_sequence": (
        "momentum_20",
        "trend_context_m3",
        "ai_horizon_alignment",
        "ai_recency_long",
        "atr_14",
    ),
}


@dataclass
class HorizonBaselineArtifact:
    horizon: str
    model_name: str
    version: str
    floor_delta: float
    ceiling_delta: float
    train_rows: int
    test_rows: int
    metrics: dict[str, float]
    params: dict[str, object] | None = None


@dataclass
class HorizonCompetitionCandidate:
    model_id: str
    model_family: str
    horizon: str
    version: str
    floor_delta: float
    ceiling_delta: float
    train_rows: int
    test_rows: int
    metrics: dict[str, float]
    params: dict[str, object]


@dataclass
class _PreparedRow:
    row: dict
    close: float
    floor_delta: float
    ceiling_delta: float
    features: dict[str, float]


def _load_rows(dataset_path: Path) -> list[dict]:
    payload = json.loads(dataset_path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
        return payload["rows"]
    if isinstance(payload, list):
        return payload
    raise ValueError(f"Unsupported dataset payload: {dataset_path}")


def _eligible(row: dict, horizon: str) -> bool:
    return row.get(f"split_eligible_{horizon}") is not False


def _split(rows: list[dict], horizon: str) -> tuple[list[dict], list[dict]]:
    """Return horizon-specific leakage-safe train and evaluation rows.

    Eligibility is authored by features.run_features from each label's actual
    target end date. This prevents train labels from reading prices belonging to
    validation/test and removes right-censored labels from every partition.
    """
    train = [
        row
        for row in rows
        if row.get("split") == "train" and _eligible(row, horizon)
    ]
    validation = [
        row
        for row in rows
        if row.get("split") == "validation" and _eligible(row, horizon)
    ]
    test = [
        row
        for row in rows
        if row.get("split") == "test" and _eligible(row, horizon)
    ]

    # Production uses validation. The test fallback exists only for historical
    # small fixtures/datasets that do not define a validation partition.
    evaluation = validation or test
    if train:
        return train, evaluation

    # Compatibility for tiny fixtures without split labels. This branch is not
    # reached by the canonical modelable dataset.
    eligible = [row for row in rows if _eligible(row, horizon)]
    pivot = max(1, int(len(eligible) * 0.7))
    return eligible[:pivot], eligible[pivot:]


def _time_order_key(row: dict) -> tuple[str, str]:
    return str(row.get("timestamp") or ""), str(row.get("symbol") or "")


def _to_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _safe_feature(row: dict, key: str, close: float) -> float:
    value = _to_float(row.get(key))
    if value is None:
        return 0.0
    if key == "atr_14":
        return value / max(close, 1.0)
    return value


def _prepare_rows(
    rows: list[dict],
    floor_col: str,
    ceiling_col: str,
    feature_names: tuple[str, ...],
) -> list[_PreparedRow]:
    ordered = sorted(rows, key=_time_order_key)
    prepared: list[_PreparedRow] = []
    prev_close_by_symbol: dict[str, float] = {}

    for row in ordered:
        close = _to_float(row.get("close"))
        floor = _to_float(row.get(floor_col))
        ceiling = _to_float(row.get(ceiling_col))
        if close is None or close <= 0 or floor is None or ceiling is None:
            continue

        floor_delta = max(0.0001, min(0.6, (close - floor) / close))
        ceiling_delta = max(0.0001, min(0.6, (ceiling - close) / close))
        features = {
            name: _safe_feature(row, name, close)
            for name in feature_names
        }
        symbol = str(row.get("symbol") or "")
        previous = prev_close_by_symbol.get(symbol)
        features["ret_1"] = (
            0.0
            if previous is None
            else (close - previous) / max(previous, 1e-6)
        )
        prev_close_by_symbol[symbol] = close
        prepared.append(
            _PreparedRow(
                row=row,
                close=close,
                floor_delta=floor_delta,
                ceiling_delta=ceiling_delta,
                features=features,
            )
        )
    return prepared


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _quantiles(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(
        0,
        min(len(ordered) - 1, int(round((len(ordered) - 1) * quantile))),
    )
    return float(ordered[idx])


def _clamp_delta(value: float) -> float:
    return max(0.0001, min(0.7, value))


def _obj_to_float(value: object, default: float = 0.0) -> float:
    parsed = _to_float(value)
    return default if parsed is None else parsed


def _obj_to_float_dict(value: object) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    return {str(key): _obj_to_float(raw) for key, raw in value.items()}


def _obj_to_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _linear_fit(
    train: list[_PreparedRow],
    feature_names: tuple[str, ...],
    target_key: str,
    l2: float = 0.01,
    lr: float = 0.02,
    epochs: int = 120,
) -> tuple[dict[str, float], float]:
    weights = {name: 0.0 for name in feature_names}
    bias = _mean([float(getattr(item, target_key)) for item in train])
    n = float(max(1, len(train)))

    for _ in range(epochs):
        grad_w = {name: 0.0 for name in feature_names}
        grad_b = 0.0
        for item in train:
            prediction = bias + sum(
                weights[name] * float(item.features.get(name, 0.0))
                for name in feature_names
            )
            error = prediction - float(getattr(item, target_key))
            grad_b += (2.0 / n) * error
            for name in feature_names:
                grad_w[name] += (
                    (2.0 / n)
                    * error
                    * float(item.features.get(name, 0.0))
                )
        for name in feature_names:
            grad_w[name] += 2.0 * l2 * weights[name]
            weights[name] -= lr * grad_w[name]
        bias -= lr * grad_b
    return weights, float(bias)


def _predict_linear(
    item: _PreparedRow,
    weights: dict[str, float],
    bias: float,
    feature_names: tuple[str, ...],
) -> float:
    return _clamp_delta(
        bias
        + sum(
            weights[name] * float(item.features.get(name, 0.0))
            for name in feature_names
        )
    )


def _fit_evt(
    train: list[_PreparedRow],
    target_key: str,
    bins: int = 3,
) -> dict[str, object]:
    if not train:
        return {"global": 0.01, "table": {}, "vol_cuts": [], "bins": bins}

    vol_values = [abs(float(item.features.get("atr_14", 0.0))) for item in train]
    cuts = [_quantiles(vol_values, i / bins) for i in range(1, bins)]

    def bucket(volatility: float) -> int:
        for idx, cut in enumerate(cuts, start=1):
            if volatility <= cut:
                return idx
        return bins

    grouped: dict[str, list[float]] = {}
    for item in train:
        trend_bucket = (
            "up"
            if float(item.features.get("trend_context_m3", 0.0)) >= 0
            else "down"
        )
        vol_bucket = bucket(abs(float(item.features.get("atr_14", 0.0))))
        key = f"v{vol_bucket}:{trend_bucket}"
        grouped.setdefault(key, []).append(float(getattr(item, target_key)))

    return {
        "global": _quantiles(
            [float(getattr(item, target_key)) for item in train],
            0.5,
        ),
        "table": {
            key: _quantiles(values, 0.5)
            for key, values in grouped.items()
        },
        "vol_cuts": cuts,
        "bins": bins,
    }


def _predict_evt(item: _PreparedRow, params: dict[str, object]) -> float:
    bins = int(_obj_to_float(params.get("bins"), 3.0)) or 3
    cuts = [_obj_to_float(value) for value in _obj_to_list(params.get("vol_cuts"))]
    trend_bucket = (
        "up"
        if float(item.features.get("trend_context_m3", 0.0)) >= 0
        else "down"
    )
    volatility = abs(float(item.features.get("atr_14", 0.0)))
    vol_bucket = bins
    for idx, cut in enumerate(cuts, start=1):
        if volatility <= cut:
            vol_bucket = idx
            break
    table = _obj_to_float_dict(params.get("table"))
    key = f"v{vol_bucket}:{trend_bucket}"
    return _clamp_delta(
        _obj_to_float(table.get(key, params.get("global", 0.01)), 0.01)
    )


def _fit_boosted_stumps(
    train: list[_PreparedRow],
    feature_names: tuple[str, ...],
    target_key: str,
    rounds: int = 6,
    lr: float = 0.45,
) -> dict[str, object]:
    if not train:
        return {"base": 0.01, "stumps": [], "lr": lr, "rounds": rounds}

    targets = [float(getattr(item, target_key)) for item in train]
    base = _mean(targets)
    predictions = [base for _ in train]
    stumps: list[dict[str, float | str]] = []

    for _ in range(rounds):
        residuals = [target - prediction for target, prediction in zip(targets, predictions)]
        best: dict[str, float | str] | None = None
        best_error = float("inf")

        for feature in feature_names:
            values = [float(item.features.get(feature, 0.0)) for item in train]
            for threshold in [_quantiles(values, q) for q in (0.2, 0.4, 0.6, 0.8)]:
                left = [
                    residual
                    for residual, value in zip(residuals, values)
                    if value <= threshold
                ]
                right = [
                    residual
                    for residual, value in zip(residuals, values)
                    if value > threshold
                ]
                if not left or not right:
                    continue
                left_value = _mean(left)
                right_value = _mean(right)
                error = sum(
                    (
                        residual
                        - (left_value if value <= threshold else right_value)
                    )
                    ** 2
                    for residual, value in zip(residuals, values)
                )
                if error < best_error:
                    best_error = error
                    best = {
                        "feature": feature,
                        "threshold": float(threshold),
                        "left": float(left_value),
                        "right": float(right_value),
                    }

        if best is None:
            break
        stumps.append(best)
        feature = str(best["feature"])
        threshold = float(best["threshold"])
        left_value = float(best["left"])
        right_value = float(best["right"])
        for idx, item in enumerate(train):
            predictions[idx] += lr * (
                left_value
                if float(item.features.get(feature, 0.0)) <= threshold
                else right_value
            )

    return {"base": base, "stumps": stumps, "lr": lr, "rounds": rounds}


def _predict_boosted_stumps(
    item: _PreparedRow,
    params: dict[str, object],
) -> float:
    prediction = _obj_to_float(params.get("base"), 0.01)
    lr = _obj_to_float(params.get("lr"), 0.45)
    for raw_stump in _obj_to_list(params.get("stumps")):
        if not isinstance(raw_stump, dict):
            continue
        feature = str(raw_stump.get("feature") or "")
        threshold = _obj_to_float(raw_stump.get("threshold"))
        left = _obj_to_float(raw_stump.get("left"))
        right = _obj_to_float(raw_stump.get("right"))
        prediction += lr * (
            left
            if float(item.features.get(feature, 0.0)) <= threshold
            else right
        )
    return _clamp_delta(prediction)


def _family_model(
    family: str,
    train_rows: list[_PreparedRow],
    target_key: str,
    training_mode: str,
) -> tuple[dict[str, object], Callable[[_PreparedRow], float]]:
    """Fit one current baseline family without unsafe inner temporal CV.

    Horizon-specific purge/embargo is handled at dataset split eligibility. A
    future fold-level tuner must carry the same target-end contract before it is
    enabled for real ABT rows.
    """
    if family == "evt_changepoint_hybrid":
        params = _fit_evt(train_rows, target_key=target_key, bins=3)
        params["cv"] = {
            "enabled": False,
            "reason": "disabled_until_fold_level_purge_contract",
        }
        return params, lambda item: _predict_evt(item, params)

    if family == "xgboost":
        feature_names = FEATURES_BY_FAMILY["xgboost"]
        params = _fit_boosted_stumps(
            train_rows,
            feature_names,
            target_key,
            rounds=6,
            lr=0.45,
        )
        params["cv"] = {
            "enabled": False,
            "reason": "disabled_until_fold_level_purge_contract",
        }
        return params, lambda item: _predict_boosted_stumps(item, params)

    feature_key = "lstm_sequence" if family == "lstm_sequence" else "qenet"
    feature_names = FEATURES_BY_FAMILY[feature_key]
    weights, bias = _linear_fit(
        train_rows,
        feature_names,
        target_key,
        l2=0.01,
        lr=0.02,
        epochs=120,
    )
    params: dict[str, object] = {
        "weights": weights,
        "bias": bias,
        "features": list(feature_names),
        "l2": 0.01,
        "lr": 0.02,
        "cv": {
            "enabled": False,
            "reason": "disabled_until_fold_level_purge_contract",
        },
    }
    return (
        params,
        lambda item: _predict_linear(
            item,
            _obj_to_float_dict(params.get("weights")),
            _obj_to_float(params.get("bias")),
            feature_names,
        ),
    )


def _evaluate_predictions(
    test_rows: list[_PreparedRow],
    pred_floor_delta: list[float],
    pred_ceiling_delta: list[float],
) -> dict[str, float]:
    floor_errors: list[float] = []
    ceiling_errors: list[float] = []
    spread_errors: list[float] = []

    for item, floor_delta, ceiling_delta in zip(
        test_rows,
        pred_floor_delta,
        pred_ceiling_delta,
    ):
        predicted_floor = item.close * (1.0 - floor_delta)
        predicted_ceiling = item.close * (1.0 + ceiling_delta)
        true_floor = item.close * (1.0 - item.floor_delta)
        true_ceiling = item.close * (1.0 + item.ceiling_delta)
        floor_errors.append(abs(predicted_floor - true_floor))
        ceiling_errors.append(abs(predicted_ceiling - true_ceiling))
        spread_errors.append(
            abs(
                (predicted_ceiling - predicted_floor)
                - (true_ceiling - true_floor)
            )
        )

    return {
        "mae_floor": _mean(floor_errors),
        "mae_ceiling": _mean(ceiling_errors),
        "mae_spread": _mean(spread_errors),
        "test_floor_coverage": (
            len(floor_errors) / max(1, len(test_rows))
        ),
        "test_ceiling_coverage": (
            len(ceiling_errors) / max(1, len(test_rows))
        ),
    }


def train_horizon_competition(
    rows: list[dict],
    horizon: str,
    version: str,
    training_mode: str = "standard",
) -> tuple[list[HorizonCompetitionCandidate], HorizonCompetitionCandidate]:
    if horizon not in HORIZON_TARGETS:
        raise ValueError(f"Unsupported horizon: {horizon}")

    floor_col, ceiling_col = HORIZON_TARGETS[horizon]
    train_rows_raw, evaluation_rows_raw = _split(rows, horizon)
    family_features = tuple(
        sorted(
            {
                name
                for features in FEATURES_BY_FAMILY.values()
                for name in features
            }
        )
    )
    train_prepared = _prepare_rows(
        train_rows_raw,
        floor_col,
        ceiling_col,
        family_features,
    )
    evaluation_prepared = _prepare_rows(
        evaluation_rows_raw,
        floor_col,
        ceiling_col,
        family_features,
    )

    if not train_prepared:
        raise ValueError(
            f"No leakage-safe training rows with valid labels for horizon={horizon}"
        )
    if not evaluation_prepared:
        # Unit-test compatibility only. Canonical datasets should always retain
        # an eligible validation slice for d1/w1/q1.
        evaluation_prepared = train_prepared[-max(1, len(train_prepared) // 3) :]

    timing_params = fit_horizon_timing(train_rows_raw, horizon)
    candidates: list[HorizonCompetitionCandidate] = []

    for spec in [spec for spec in build_model_specs() if spec.horizon == horizon]:
        floor_params, floor_fn = _family_model(
            spec.model_family,
            train_prepared,
            target_key="floor_delta",
            training_mode=training_mode,
        )
        ceiling_params, ceiling_fn = _family_model(
            spec.model_family,
            train_prepared,
            target_key="ceiling_delta",
            training_mode=training_mode,
        )
        pred_floor_delta = [floor_fn(item) for item in evaluation_prepared]
        pred_ceiling_delta = [ceiling_fn(item) for item in evaluation_prepared]
        metrics = _evaluate_predictions(
            evaluation_prepared,
            pred_floor_delta,
            pred_ceiling_delta,
        )
        candidates.append(
            HorizonCompetitionCandidate(
                model_id=spec.model_id,
                model_family=spec.model_family,
                horizon=horizon,
                version=version,
                floor_delta=float(
                    _clamp_delta(_quantiles(pred_floor_delta, 0.5))
                ),
                ceiling_delta=float(
                    _clamp_delta(_quantiles(pred_ceiling_delta, 0.5))
                ),
                train_rows=len(train_prepared),
                test_rows=len(evaluation_prepared),
                metrics=metrics,
                params={
                    "schema_version": 2,
                    "floor": floor_params,
                    "ceiling": ceiling_params,
                    "timing": timing_params,
                    "training_mode": training_mode,
                    "split_integrity": {
                        "eligibility_field": f"split_eligible_{horizon}",
                        "train_rows_raw": len(train_rows_raw),
                        "evaluation_rows_raw": len(evaluation_rows_raw),
                    },
                },
            )
        )

    champion = min(
        candidates,
        key=lambda candidate: (
            candidate.metrics["mae_spread"],
            candidate.metrics["mae_floor"]
            + candidate.metrics["mae_ceiling"],
        ),
    )
    return candidates, champion


def run(
    dataset_path: Path,
    output_dir: Path,
    version: str,
    tasks: tuple[str, ...] | list[str] | None = None,
    training_mode: str = "standard",
) -> Path:
    rows = _load_rows(dataset_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    selected_horizons = list(
        HORIZONS
        if not tasks
        else [horizon for horizon in tasks if horizon in HORIZONS]
    )
    if not selected_horizons:
        raise ValueError(
            "No valid horizon tasks requested. Use one or more of: d1,w1,q1"
        )

    artifacts: list[HorizonBaselineArtifact] = []
    for horizon in selected_horizons:
        candidates, champion_candidate = train_horizon_competition(
            rows,
            horizon=horizon,
            version=version,
            training_mode=training_mode,
        )
        artifact = HorizonBaselineArtifact(
            horizon=horizon,
            model_name=champion_candidate.model_id,
            version=version,
            floor_delta=champion_candidate.floor_delta,
            ceiling_delta=champion_candidate.ceiling_delta,
            train_rows=champion_candidate.train_rows,
            test_rows=champion_candidate.test_rows,
            metrics=champion_candidate.metrics,
            params=champion_candidate.params,
        )
        artifacts.append(artifact)
        (output_dir / f"{horizon}_champion.json").write_text(
            json.dumps(asdict(artifact), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (output_dir / f"{horizon}_competition.json").write_text(
            json.dumps(
                {
                    "horizon": horizon,
                    "version": version,
                    "selection_metric": "mae_spread_then_total_mae",
                    "training_mode": training_mode,
                    "selected_model_id": champion_candidate.model_id,
                    "timing_status": (
                        champion_candidate.params.get("timing", {}).get("status")
                        if isinstance(champion_candidate.params.get("timing"), dict)
                        else "unknown"
                    ),
                    "candidates": [asdict(candidate) for candidate in candidates],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        logger.info(
            "[horizon-training] horizon=%s champion=%s train_rows=%s evaluation_rows=%s timing=%s mode=%s",
            horizon,
            artifact.model_name,
            artifact.train_rows,
            artifact.test_rows,
            (
                artifact.params.get("timing", {}).get("status")
                if isinstance(artifact.params, dict)
                and isinstance(artifact.params.get("timing"), dict)
                else "unknown"
            ),
            training_mode,
        )

    csv_path = output_dir / f"horizon_training_results_{version}.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "horizon",
                "model_name",
                "version",
                "train_rows",
                "test_rows",
                "floor_delta",
                "ceiling_delta",
                "mae_floor",
                "mae_ceiling",
                "mae_spread",
                "test_floor_coverage",
                "test_ceiling_coverage",
                "timing_status",
            ],
        )
        writer.writeheader()
        for artifact in artifacts:
            timing_status = "unknown"
            if isinstance(artifact.params, dict):
                timing = artifact.params.get("timing")
                if isinstance(timing, dict):
                    timing_status = str(timing.get("status") or "unknown")
            writer.writerow(
                {
                    "horizon": artifact.horizon,
                    "model_name": artifact.model_name,
                    "version": artifact.version,
                    "train_rows": artifact.train_rows,
                    "test_rows": artifact.test_rows,
                    "floor_delta": round(artifact.floor_delta, 8),
                    "ceiling_delta": round(artifact.ceiling_delta, 8),
                    "mae_floor": round(float(artifact.metrics["mae_floor"]), 8),
                    "mae_ceiling": round(float(artifact.metrics["mae_ceiling"]), 8),
                    "mae_spread": round(float(artifact.metrics["mae_spread"]), 8),
                    "test_floor_coverage": round(float(artifact.metrics["test_floor_coverage"]), 8),
                    "test_ceiling_coverage": round(float(artifact.metrics["test_ceiling_coverage"]), 8),
                    "timing_status": timing_status,
                }
            )

    logger.info("[horizon-training] wrote csv summary=%s", csv_path)
    return csv_path


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    parser = argparse.ArgumentParser(
        description="Train leakage-safe d1/w1/q1 floor/ceiling and timing champions"
    )
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output-dir", default="data/training/models")
    parser.add_argument("--version", required=True)
    parser.add_argument("--tasks", default="d1,w1,q1")
    parser.add_argument(
        "--training-mode",
        default="standard",
        choices=["standard", "manual", "retrain", "renewal"],
    )
    args = parser.parse_args()
    tasks = tuple(
        part.strip()
        for part in str(args.tasks).split(",")
        if part.strip()
    )
    run(
        Path(args.dataset),
        Path(args.output_dir),
        args.version,
        tasks=tasks,
        training_mode=args.training_mode,
    )


if __name__ == "__main__":
    main()
