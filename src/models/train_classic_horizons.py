from __future__ import annotations

import argparse
import csv
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from features.model_competition import HORIZONS, build_model_specs
from models.horizon_timing import fit_horizon_timing
from models.robust_range_v3 import (
    ROBUST_RANGE_FEATURES,
    build_anchored_blend,
    feature_value as robust_feature_value,
    fit_ceiling_head,
    fit_floor_head,
    predict_head as predict_robust_head,
)

logger = logging.getLogger(__name__)

HORIZON_TARGETS = {
    "d1": ("floor_d1", "ceiling_d1"),
    "w1": ("floor_w1", "ceiling_w1"),
    "q1": ("floor_q1", "ceiling_q1"),
}

FEATURES_BY_FAMILY: dict[str, tuple[str, ...]] = {
    "robust_range_v3": ROBUST_RANGE_FEATURES,
    "regularized_linear": (
        "atr_14",
        "trend_context_m3",
        "drawdown_13w",
        "dist_to_low_3m",
        "ai_horizon_alignment",
    ),
    "boosted_stumps": (
        "atr_14",
        "trend_context_m3",
        "drawdown_13w",
        "dist_to_low_3m",
        "ai_horizon_alignment",
        "rel_strength_20",
    ),
    "sequence_linear": (
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
    params: dict[str, Any] | None = None


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
    params: dict[str, Any]


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
    """Return training and champion-selection validation rows.

    If the ABT contains explicit chronological splits, only ``validation`` is
    allowed for model selection. ``test`` is intentionally untouched for final
    assessment/backtesting. For small synthetic/unit-test inputs without split
    metadata, a deterministic 70/30 chronological holdout is created.
    """

    explicit_split = any(
        row.get("split") in {"train", "validation", "test"} for row in rows
    )
    if explicit_split:
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
        return train, validation

    eligible = [row for row in rows if _eligible(row, horizon)]
    if len(eligible) < 2:
        return eligible, []
    ordered = sorted(
        eligible,
        key=lambda row: (
            str(row.get("timestamp") or ""),
            str(row.get("symbol") or ""),
        ),
    )
    pivot = max(1, min(len(ordered) - 1, int(len(ordered) * 0.7)))
    return ordered[:pivot], ordered[pivot:]


def _number(value: object) -> float | None:
    if not isinstance(value, (int, float, str, bytes, bytearray)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _feature(row: dict, name: str, close: float) -> float:
    if name in ROBUST_RANGE_FEATURES:
        return robust_feature_value(row, name, close)
    value = _number(row.get(name))
    if value is None:
        return 0.0
    return value / max(close, 1.0) if name == "atr_14" else value


def _prepare_rows(
    rows: list[dict],
    floor_col: str,
    ceiling_col: str,
    feature_names: tuple[str, ...],
) -> list[_PreparedRow]:
    ordered = sorted(
        rows,
        key=lambda row: (
            str(row.get("timestamp") or ""),
            str(row.get("symbol") or ""),
        ),
    )
    previous_by_symbol: dict[str, float] = {}
    result: list[_PreparedRow] = []
    for row in ordered:
        close = _number(row.get("close"))
        floor = _number(row.get(floor_col))
        ceiling = _number(row.get(ceiling_col))
        if close is None or close <= 0 or floor is None or ceiling is None:
            continue
        features = {name: _feature(row, name, close) for name in feature_names}
        symbol = str(row.get("symbol") or "")
        previous = previous_by_symbol.get(symbol)
        features["ret_1"] = (
            0.0 if previous is None else (close - previous) / max(previous, 1e-6)
        )
        previous_by_symbol[symbol] = close
        result.append(
            _PreparedRow(
                row=row,
                close=close,
                floor_delta=max(0.0001, min(0.6, (close - floor) / close)),
                ceiling_delta=max(0.0001, min(0.6, (ceiling - close) / close)),
                features=features,
            )
        )
    return result


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * q))))
    return ordered[idx]


def _clamp(value: float) -> float:
    return max(0.0001, min(0.7, value))


def _float_dict(value: object) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    output: dict[str, float] = {}
    for key, raw in value.items():
        parsed = _number(raw)
        output[str(key)] = 0.0 if parsed is None else parsed
    return output


def _fit_evt(rows: list[_PreparedRow], target_key: str) -> dict[str, Any]:
    """Legacy function name retained for parity tests; implementation is regime median."""

    vol = [abs(item.features.get("atr_14", 0.0)) for item in rows]
    cuts = [_quantile(vol, 1 / 3), _quantile(vol, 2 / 3)] if rows else []
    grouped: dict[str, list[float]] = {}
    for item in rows:
        bucket = 3
        for idx, cut in enumerate(cuts, start=1):
            if abs(item.features.get("atr_14", 0.0)) <= cut:
                bucket = idx
                break
        trend = "up" if item.features.get("trend_context_m3", 0.0) >= 0 else "down"
        grouped.setdefault(f"v{bucket}:{trend}", []).append(
            float(getattr(item, target_key))
        )
    targets = [float(getattr(item, target_key)) for item in rows]
    return {
        "global": _quantile(targets, 0.5) if targets else 0.01,
        "table": {key: _quantile(values, 0.5) for key, values in grouped.items()},
        "vol_cuts": cuts,
        "bins": 3,
        "cv": {
            "enabled": False,
            "reason": "inner_cv_not_used_for_champion_selection",
        },
    }


def _predict_evt(item: _PreparedRow, params: dict[str, Any]) -> float:
    cuts_raw = params.get("vol_cuts")
    cuts = [float(value) for value in cuts_raw] if isinstance(cuts_raw, list) else []
    bins = int(params.get("bins") or 3)
    bucket = bins
    for idx, cut in enumerate(cuts, start=1):
        if abs(item.features.get("atr_14", 0.0)) <= cut:
            bucket = idx
            break
    trend = "up" if item.features.get("trend_context_m3", 0.0) >= 0 else "down"
    table = _float_dict(params.get("table"))
    raw = table.get(f"v{bucket}:{trend}")
    if raw is None:
        raw = float(params.get("global") or 0.01)
    return _clamp(raw)


def _fit_boosted_stumps(
    rows: list[_PreparedRow],
    feature_names: tuple[str, ...],
    target_key: str,
    rounds: int = 6,
    lr: float = 0.45,
) -> dict[str, Any]:
    targets = [float(getattr(item, target_key)) for item in rows]
    base = _mean(targets) if targets else 0.01
    predictions = [base for _ in rows]
    stumps: list[dict[str, float | str]] = []

    for _ in range(rounds):
        residuals = [
            target - prediction for target, prediction in zip(targets, predictions)
        ]
        best: dict[str, float | str] | None = None
        best_error = float("inf")
        for name in feature_names:
            values = [item.features.get(name, 0.0) for item in rows]
            threshold = _quantile(values, 0.5)
            left = [res for res, value in zip(residuals, values) if value <= threshold]
            right = [res for res, value in zip(residuals, values) if value > threshold]
            if not left or not right:
                continue
            left_value = _mean(left)
            right_value = _mean(right)
            error = sum(
                (res - (left_value if value <= threshold else right_value)) ** 2
                for res, value in zip(residuals, values)
            )
            if error < best_error:
                best_error = error
                best = {
                    "feature": name,
                    "threshold": threshold,
                    "left": left_value,
                    "right": right_value,
                }
        if best is None:
            break
        stumps.append(best)
        name = str(best["feature"])
        threshold = float(best["threshold"])
        left_value = float(best["left"])
        right_value = float(best["right"])
        for idx, item in enumerate(rows):
            predictions[idx] += lr * (
                left_value
                if item.features.get(name, 0.0) <= threshold
                else right_value
            )

    return {
        "base": base,
        "stumps": stumps,
        "lr": lr,
        "rounds": rounds,
        "cv": {
            "enabled": False,
            "reason": "inner_cv_not_used_for_champion_selection",
        },
    }


def _predict_boosted_stumps(item: _PreparedRow, params: dict[str, Any]) -> float:
    prediction = float(params.get("base") or 0.01)
    lr = float(params.get("lr") or 0.45)
    stumps = params.get("stumps")
    if not isinstance(stumps, list):
        return _clamp(prediction)
    for stump in stumps:
        if not isinstance(stump, dict):
            continue
        name = str(stump.get("feature") or "")
        threshold = float(stump.get("threshold") or 0.0)
        left = float(stump.get("left") or 0.0)
        right = float(stump.get("right") or 0.0)
        prediction += lr * (
            left if item.features.get(name, 0.0) <= threshold else right
        )
    return _clamp(prediction)


def _fit_linear(
    rows: list[_PreparedRow],
    feature_names: tuple[str, ...],
    target_key: str,
    *,
    l2: float,
    lr: float,
    epochs: int = 120,
) -> tuple[dict[str, float], float]:
    weights = {name: 0.0 for name in feature_names}
    bias = _mean([float(getattr(item, target_key)) for item in rows])
    n = float(max(1, len(rows)))
    for _ in range(epochs):
        grad = {name: 0.0 for name in feature_names}
        grad_bias = 0.0
        for item in rows:
            prediction = bias + sum(
                weights[name] * item.features.get(name, 0.0)
                for name in feature_names
            )
            error = prediction - float(getattr(item, target_key))
            grad_bias += 2.0 * error / n
            for name in feature_names:
                grad[name] += 2.0 * error * item.features.get(name, 0.0) / n
        for name in feature_names:
            weights[name] -= lr * (grad[name] + 2.0 * l2 * weights[name])
        bias -= lr * grad_bias
    return weights, bias


def _predict_linear(
    item: _PreparedRow,
    weights: dict[str, float],
    bias: float,
    feature_names: tuple[str, ...],
) -> float:
    return _clamp(
        bias
        + sum(
            weights.get(name, 0.0) * item.features.get(name, 0.0)
            for name in feature_names
        )
    )


def _family_model(
    family: str,
    rows: list[_PreparedRow],
    target_key: str,
    training_mode: str,
) -> tuple[dict[str, Any], Callable[[_PreparedRow], float]]:
    del training_mode
    if family == "robust_range_v3":
        challenger = (
            fit_floor_head(rows)
            if target_key == "floor_delta"
            else fit_ceiling_head(rows)
        )
        anchor = _fit_boosted_stumps(
            rows,
            FEATURES_BY_FAMILY["boosted_stumps"],
            target_key,
        )
        params = build_anchored_blend(anchor, challenger)
        return params, lambda item: _clamp(
            predict_robust_head(params, item.features, validate=False)
        )

    if family in {"regime_median", "evt_changepoint_hybrid"}:
        params = _fit_evt(rows, target_key)
        return params, lambda item: _predict_evt(item, params)

    if family in {"boosted_stumps", "xgboost"}:
        names = FEATURES_BY_FAMILY["boosted_stumps"]
        params = _fit_boosted_stumps(rows, names, target_key)
        return params, lambda item: _predict_boosted_stumps(item, params)

    if family in {"sequence_linear", "lstm_sequence"}:
        key = "sequence_linear"
        l2 = 0.02
    elif family in {"regularized_linear", "quantile_elastic_net", "qenet"}:
        key = "regularized_linear"
        l2 = 0.01
    else:
        raise ValueError(f"Unsupported classic horizon family: {family}")

    names = FEATURES_BY_FAMILY[key]
    weights, bias = _fit_linear(rows, names, target_key, l2=l2, lr=0.02)
    linear_params: dict[str, Any] = {
        "weights": weights,
        "bias": bias,
        "features": list(names),
        "l2": l2,
        "lr": 0.02,
        "cv": {
            "enabled": False,
            "reason": "inner_cv_not_used_for_champion_selection",
        },
    }
    return linear_params, lambda item: _predict_linear(item, weights, bias, names)


def _metrics(
    rows: list[_PreparedRow],
    floor_predictions: list[float],
    ceiling_predictions: list[float],
) -> dict[str, float]:
    """Evaluate boundary forecasts on a real holdout.

    Coverage is now semantic: a floor is covered when the realized minimum is
    not below the predicted floor; a ceiling is covered when the realized
    maximum is not above the predicted ceiling. Interval coverage requires both.
    """

    floor_errors: list[float] = []
    ceiling_errors: list[float] = []
    spread_errors: list[float] = []
    floor_pct_errors: list[float] = []
    ceiling_pct_errors: list[float] = []
    spread_pct_errors: list[float] = []
    floor_covered: list[float] = []
    ceiling_covered: list[float] = []
    interval_covered: list[float] = []

    for item, floor_delta, ceiling_delta in zip(
        rows, floor_predictions, ceiling_predictions
    ):
        predicted_floor = item.close * (1.0 - floor_delta)
        predicted_ceiling = item.close * (1.0 + ceiling_delta)
        actual_floor = item.close * (1.0 - item.floor_delta)
        actual_ceiling = item.close * (1.0 + item.ceiling_delta)
        floor_error = abs(predicted_floor - actual_floor)
        ceiling_error = abs(predicted_ceiling - actual_ceiling)
        spread_error = abs(
            (predicted_ceiling - predicted_floor) - (actual_ceiling - actual_floor)
        )
        floor_errors.append(floor_error)
        ceiling_errors.append(ceiling_error)
        spread_errors.append(spread_error)
        denom = max(item.close, 1e-6)
        floor_pct_errors.append(floor_error / denom)
        ceiling_pct_errors.append(ceiling_error / denom)
        spread_pct_errors.append(spread_error / denom)
        floor_ok = actual_floor >= predicted_floor
        ceiling_ok = actual_ceiling <= predicted_ceiling
        floor_covered.append(1.0 if floor_ok else 0.0)
        ceiling_covered.append(1.0 if ceiling_ok else 0.0)
        interval_covered.append(1.0 if floor_ok and ceiling_ok else 0.0)

    interval_coverage = _mean(interval_covered)
    return {
        "mae_floor": _mean(floor_errors),
        "mae_ceiling": _mean(ceiling_errors),
        "mae_spread": _mean(spread_errors),
        "mae_floor_pct": _mean(floor_pct_errors),
        "mae_ceiling_pct": _mean(ceiling_pct_errors),
        "mae_spread_pct": _mean(spread_pct_errors),
        "test_floor_coverage": _mean(floor_covered),
        "test_ceiling_coverage": _mean(ceiling_covered),
        "test_interval_coverage": interval_coverage,
        "empirical_breach_rate": 1.0 - interval_coverage,
    }


def train_horizon_competition(
    rows: list[dict],
    horizon: str,
    version: str,
    training_mode: str = "standard",
    model_families: tuple[str, ...] | list[str] | None = None,
) -> tuple[list[HorizonCompetitionCandidate], HorizonCompetitionCandidate]:
    if horizon not in HORIZON_TARGETS:
        raise ValueError(f"Unsupported horizon: {horizon}")

    floor_col, ceiling_col = HORIZON_TARGETS[horizon]
    train_raw, evaluation_raw = _split(rows, horizon)
    feature_names = tuple(
        sorted({name for values in FEATURES_BY_FAMILY.values() for name in values})
    )
    train = _prepare_rows(train_raw, floor_col, ceiling_col, feature_names)
    evaluation = _prepare_rows(evaluation_raw, floor_col, ceiling_col, feature_names)
    if not train:
        raise ValueError(
            f"No leakage-safe training rows with valid labels for horizon={horizon}"
        )
    if not evaluation:
        raise ValueError(
            f"No leakage-safe validation rows for horizon={horizon}; "
            "champion selection refuses train/test fallback"
        )

    timing = fit_horizon_timing(train_raw, horizon)
    candidates: list[HorizonCompetitionCandidate] = []
    allowed_families = set(model_families or ())
    specs = [
        spec
        for spec in build_model_specs()
        if spec.horizon == horizon
        and (not allowed_families or spec.model_family in allowed_families)
    ]
    if not specs:
        raise ValueError(f"No model families selected for horizon={horizon}")
    for spec in specs:
        floor_params, floor_fn = _family_model(
            spec.model_family, train, "floor_delta", training_mode
        )
        ceiling_params, ceiling_fn = _family_model(
            spec.model_family, train, "ceiling_delta", training_mode
        )
        floor_predictions = [floor_fn(item) for item in evaluation]
        ceiling_predictions = [ceiling_fn(item) for item in evaluation]
        candidate_metrics = _metrics(evaluation, floor_predictions, ceiling_predictions)
        candidates.append(
            HorizonCompetitionCandidate(
                model_id=spec.model_id,
                model_family=spec.model_family,
                horizon=horizon,
                version=version,
                floor_delta=_clamp(_quantile(floor_predictions, 0.5)),
                ceiling_delta=_clamp(_quantile(ceiling_predictions, 0.5)),
                train_rows=len(train),
                test_rows=len(evaluation),
                metrics=candidate_metrics,
                params={
                    "schema_version": 2,
                    "floor": floor_params,
                    "ceiling": ceiling_params,
                    "timing": timing,
                    "training_mode": training_mode,
                    "confidence_calibration": {
                        "method": "validation_empirical_interval_breach",
                        "breach_probability": candidate_metrics["empirical_breach_rate"],
                        "evaluation_rows": len(evaluation),
                    },
                    "split_integrity": {
                        "eligibility_field": f"split_eligible_{horizon}",
                        "selection_split": "validation",
                        "test_used_for_selection": False,
                        "train_rows_raw": len(train_raw),
                        "evaluation_rows_raw": len(evaluation_raw),
                    },
                },
            )
        )

    champion = min(
        candidates,
        key=lambda item: (
            item.metrics["mae_spread_pct"],
            item.metrics["mae_floor_pct"] + item.metrics["mae_ceiling_pct"],
        ),
    )
    return candidates, champion


def run(
    dataset_path: Path,
    output_dir: Path,
    version: str,
    tasks: tuple[str, ...] | list[str] | None = None,
    training_mode: str = "standard",
    model_families: tuple[str, ...] | list[str] | None = None,
) -> Path:
    rows = _load_rows(dataset_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    selected = list(
        HORIZONS
        if not tasks
        else [horizon for horizon in tasks if horizon in HORIZONS]
    )
    if not selected:
        raise ValueError("No valid horizon tasks requested. Use d1,w1,q1")

    artifacts: list[HorizonBaselineArtifact] = []
    for horizon in selected:
        candidates, champion = train_horizon_competition(
            rows, horizon, version, training_mode, model_families
        )
        artifact = HorizonBaselineArtifact(
            horizon=horizon,
            model_name=champion.model_id,
            version=version,
            floor_delta=champion.floor_delta,
            ceiling_delta=champion.ceiling_delta,
            train_rows=champion.train_rows,
            test_rows=champion.test_rows,
            metrics=champion.metrics,
            params=champion.params,
        )
        artifacts.append(artifact)
        (output_dir / f"{horizon}_champion.json").write_text(
            json.dumps(asdict(artifact), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        timing_status = "unknown"
        timing_payload = champion.params.get("timing")
        if isinstance(timing_payload, dict):
            timing_status = str(timing_payload.get("status") or "unknown")
        (output_dir / f"{horizon}_competition.json").write_text(
            json.dumps(
                {
                    "horizon": horizon,
                    "version": version,
                    "selection_metric": "mae_spread_pct_then_total_boundary_mae_pct",
                    "selection_split": "validation",
                    "test_used_for_selection": False,
                    "training_mode": training_mode,
                    "model_families": list(model_families or ()),
                    "selected_model_id": champion.model_id,
                    "timing_status": timing_status,
                    "candidates": [asdict(candidate) for candidate in candidates],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    csv_path = output_dir / f"horizon_training_results_{version}.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        fields = [
            "horizon",
            "model_name",
            "version",
            "train_rows",
            "validation_rows",
            "floor_delta",
            "ceiling_delta",
            "mae_floor",
            "mae_ceiling",
            "mae_spread",
            "mae_floor_pct",
            "mae_ceiling_pct",
            "mae_spread_pct",
            "test_floor_coverage",
            "test_ceiling_coverage",
            "test_interval_coverage",
            "empirical_breach_rate",
            "timing_status",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for artifact in artifacts:
            timing_status = "unknown"
            if isinstance(artifact.params, dict):
                timing_payload = artifact.params.get("timing")
                if isinstance(timing_payload, dict):
                    timing_status = str(timing_payload.get("status") or "unknown")
            writer.writerow(
                {
                    "horizon": artifact.horizon,
                    "model_name": artifact.model_name,
                    "version": artifact.version,
                    "train_rows": artifact.train_rows,
                    "validation_rows": artifact.test_rows,
                    "floor_delta": round(artifact.floor_delta, 8),
                    "ceiling_delta": round(artifact.ceiling_delta, 8),
                    "mae_floor": round(artifact.metrics["mae_floor"], 8),
                    "mae_ceiling": round(artifact.metrics["mae_ceiling"], 8),
                    "mae_spread": round(artifact.metrics["mae_spread"], 8),
                    "mae_floor_pct": round(artifact.metrics["mae_floor_pct"], 8),
                    "mae_ceiling_pct": round(artifact.metrics["mae_ceiling_pct"], 8),
                    "mae_spread_pct": round(artifact.metrics["mae_spread_pct"], 8),
                    "test_floor_coverage": round(
                        artifact.metrics["test_floor_coverage"], 8
                    ),
                    "test_ceiling_coverage": round(
                        artifact.metrics["test_ceiling_coverage"], 8
                    ),
                    "test_interval_coverage": round(
                        artifact.metrics["test_interval_coverage"], 8
                    ),
                    "empirical_breach_rate": round(
                        artifact.metrics["empirical_breach_rate"], 8
                    ),
                    "timing_status": timing_status,
                }
            )
    return csv_path


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output-dir", default="data/training/models")
    parser.add_argument("--version", required=True)
    parser.add_argument("--tasks", default="d1,w1,q1")
    parser.add_argument(
        "--training-mode",
        default="standard",
        choices=["standard", "manual", "retrain", "renewal"],
    )
    parser.add_argument(
        "--families",
        default="",
        help="Optional comma-separated model-family allowlist",
    )
    args = parser.parse_args()
    tasks = tuple(
        part.strip() for part in str(args.tasks).split(",") if part.strip()
    )
    families = tuple(
        part.strip() for part in str(args.families).split(",") if part.strip()
    )
    run(
        Path(args.dataset),
        Path(args.output_dir),
        args.version,
        tasks,
        args.training_mode,
        families,
    )


if __name__ == "__main__":
    main()
