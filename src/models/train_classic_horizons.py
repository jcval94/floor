from __future__ import annotations

import argparse
import csv
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from features.model_competition import HORIZONS, build_model_specs

logger = logging.getLogger(__name__)

HORIZON_TARGETS = {
    "d1": ("floor_d1", "ceiling_d1"),
    "w1": ("floor_w1", "ceiling_w1"),
    "q1": ("floor_q1", "ceiling_q1"),
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


FEATURES_BY_FAMILY: dict[str, tuple[str, ...]] = {
    "qenet": ("atr_14", "trend_context_m3", "drawdown_13w", "dist_to_low_3m", "ai_horizon_alignment"),
    "xgboost": ("atr_14", "trend_context_m3", "drawdown_13w", "dist_to_low_3m", "ai_horizon_alignment", "rel_strength_20"),
    "lstm_sequence": ("momentum_20", "trend_context_m3", "ai_horizon_alignment", "ai_recency_long", "atr_14"),
}


@dataclass
class _PreparedRow:
    row: dict
    close: float
    floor_delta: float
    ceiling_delta: float
    features: dict[str, float]


def _load_rows(dataset_path: Path) -> list[dict]:
    payload = json.loads(dataset_path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "rows" in payload:
        rows = payload["rows"]
    elif isinstance(payload, list):
        rows = payload
    else:
        raise ValueError(f"Unsupported dataset payload: {dataset_path}")
    if not isinstance(rows, list):
        raise ValueError("Dataset rows must be a list")
    return rows


def _split(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    train = [r for r in rows if r.get("split") == "train"]
    test = [r for r in rows if r.get("split") in {"validation", "test"}]
    if not train:
        pivot = int(len(rows) * 0.7)
        train = rows[:pivot]
        test = rows[pivot:]
    return train, test


def _time_order_key(row: dict) -> tuple[str, str]:
    return str(row.get("timestamp") or ""), str(row.get("symbol") or "")


def _to_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
    if not isinstance(value, (int, float, str, bytes, bytearray)):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _safe_feature(row: dict, key: str, close: float) -> float:
    value = _to_float(row.get(key))
    if value is None:
        return 0.0
    if key in {"atr_14"}:
        return float(value) / max(close, 1.0)
    return float(value)


def _prepare_rows(rows: list[dict], floor_col: str, ceiling_col: str, feature_names: tuple[str, ...]) -> list[_PreparedRow]:
    ordered = sorted(rows, key=_time_order_key)
    prepared: list[_PreparedRow] = []
    prev_close: float | None = None
    for row in ordered:
        close = _to_float(row.get("close"))
        floor = _to_float(row.get(floor_col))
        ceiling = _to_float(row.get(ceiling_col))
        if close is None or close <= 0 or floor is None or ceiling is None:
            prev_close = close if close is not None and close > 0 else prev_close
            continue
        floor_delta = max(0.0001, min(0.6, (close - floor) / close))
        ceiling_delta = max(0.0001, min(0.6, (ceiling - close) / close))
        features = {name: _safe_feature(row, name, close) for name in feature_names}
        if prev_close is None:
            features["ret_1"] = 0.0
        else:
            features["ret_1"] = (close - prev_close) / max(prev_close, 1e-6)
        prepared.append(_PreparedRow(row=row, close=close, floor_delta=floor_delta, ceiling_delta=ceiling_delta, features=features))
        prev_close = close
    return prepared


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _quantiles(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * quantile))))
    return float(ordered[idx])


def _expanding_folds(rows: list[_PreparedRow], folds: int = 3) -> list[tuple[list[_PreparedRow], list[_PreparedRow]]]:
    if len(rows) < max(24, folds * 6):
        return []
    fold_size = max(4, len(rows) // (folds + 1))
    result: list[tuple[list[_PreparedRow], list[_PreparedRow]]] = []
    for i in range(1, folds + 1):
        train_end = max(fold_size, i * fold_size)
        valid_end = min(len(rows), train_end + fold_size)
        train = rows[:train_end]
        valid = rows[train_end:valid_end]
        if train and valid:
            result.append((train, valid))
    return result


def _clamp_delta(x: float) -> float:
    return max(0.0001, min(0.7, x))


def _linear_fit(train: list[_PreparedRow], feature_names: tuple[str, ...], target_key: str, l2: float = 0.01, lr: float = 0.03, epochs: int = 120) -> tuple[dict[str, float], float]:
    weights = {name: 0.0 for name in feature_names}
    bias = _mean([getattr(item, target_key) for item in train])
    n = float(max(1, len(train)))
    for _ in range(epochs):
        grad_w = {name: 0.0 for name in feature_names}
        grad_b = 0.0
        for item in train:
            pred = bias + sum(weights[name] * float(item.features.get(name, 0.0)) for name in feature_names)
            err = pred - float(getattr(item, target_key))
            grad_b += (2.0 / n) * err
            for name in feature_names:
                grad_w[name] += (2.0 / n) * err * float(item.features.get(name, 0.0))
        for name in feature_names:
            grad_w[name] += 2.0 * l2 * weights[name]
            weights[name] -= lr * grad_w[name]
        bias -= lr * grad_b
    return weights, float(bias)


def _predict_linear(item: _PreparedRow, weights: dict[str, float], bias: float, feature_names: tuple[str, ...]) -> float:
    return _clamp_delta(bias + sum(weights[name] * float(item.features.get(name, 0.0)) for name in feature_names))


def _fit_evt(train: list[_PreparedRow], target_key: str, bins: int = 3) -> dict[str, object]:
    if not train:
        return {"global": 0.01, "table": {}}
    vol_values = [abs(float(x.features.get("atr_14", 0.0))) for x in train]
    cuts = [_quantiles(vol_values, i / bins) for i in range(1, bins)]

    def bucket(vol: float) -> int:
        for idx, cut in enumerate(cuts, start=1):
            if vol <= cut:
                return idx
        return bins

    table: dict[str, float] = {}
    grouped: dict[str, list[float]] = {}
    for item in train:
        trend = float(item.features.get("trend_context_m3", 0.0))
        trend_bucket = "up" if trend >= 0 else "down"
        vol_bucket = bucket(abs(float(item.features.get("atr_14", 0.0))))
        key = f"v{vol_bucket}:{trend_bucket}"
        grouped.setdefault(key, []).append(float(getattr(item, target_key)))

    for key, vals in grouped.items():
        table[key] = _quantiles(vals, 0.5)

    global_med = _quantiles([float(getattr(item, target_key)) for item in train], 0.5)
    return {"global": global_med, "table": table, "vol_cuts": cuts, "bins": bins}


def _predict_evt(item: _PreparedRow, params: dict[str, object]) -> float:
    bins_raw = params.get("bins", 3)
    bins = int(bins_raw) if isinstance(bins_raw, (int, float, str)) else 3

    cuts_raw = params.get("vol_cuts", [])
    cuts: list[float] = []
    if isinstance(cuts_raw, list):
        for x in cuts_raw:
            if isinstance(x, (int, float, str)):
                cuts.append(float(x))

    trend = float(item.features.get("trend_context_m3", 0.0))
    trend_bucket = "up" if trend >= 0 else "down"
    vol = abs(float(item.features.get("atr_14", 0.0)))
    vol_bucket = bins
    for idx, cut in enumerate(cuts, start=1):
        if vol <= cut:
            vol_bucket = idx
            break
    key = f"v{vol_bucket}:{trend_bucket}"

    table_raw = params.get("table", {})
    table: dict[str, float] = {}
    if isinstance(table_raw, dict):
        for k, v in table_raw.items():
            if isinstance(k, str) and isinstance(v, (int, float, str)):
                table[k] = float(v)

    global_raw = params.get("global", 0.01)
    global_default = float(global_raw) if isinstance(global_raw, (int, float, str)) else 0.01
    pred = table.get(key, global_default)
    return _clamp_delta(pred)


def _fit_boosted_stumps(train: list[_PreparedRow], feature_names: tuple[str, ...], target_key: str, rounds: int = 6, lr: float = 0.45) -> dict[str, object]:
    if not train:
        return {"base": 0.01, "stumps": []}
    y = [float(getattr(item, target_key)) for item in train]
    base = _mean(y)
    preds = [base for _ in train]
    stumps: list[dict[str, float | str]] = []

    for _ in range(rounds):
        residuals = [yy - pp for yy, pp in zip(y, preds)]
        best: dict[str, float | str] | None = None
        best_err = float("inf")
        for feat in feature_names:
            values = [float(item.features.get(feat, 0.0)) for item in train]
            thresholds = [_quantiles(values, q) for q in (0.2, 0.4, 0.6, 0.8)]
            for thr in thresholds:
                left = [res for res, val in zip(residuals, values) if val <= thr]
                right = [res for res, val in zip(residuals, values) if val > thr]
                if not left or not right:
                    continue
                left_val = _mean(left)
                right_val = _mean(right)
                err = 0.0
                for res, val in zip(residuals, values):
                    pred = left_val if val <= thr else right_val
                    err += (res - pred) ** 2
                if err < best_err:
                    best_err = err
                    best = {"feature": feat, "threshold": float(thr), "left": float(left_val), "right": float(right_val)}
        if best is None:
            break
        stumps.append(best)
        feat = str(best["feature"])
        thr = float(best["threshold"])
        left_val = float(best["left"])
        right_val = float(best["right"])
        for idx, item in enumerate(train):
            preds[idx] += lr * (left_val if float(item.features.get(feat, 0.0)) <= thr else right_val)

    return {"base": base, "stumps": stumps, "lr": lr, "rounds": rounds}


def _predict_boosted_stumps(item: _PreparedRow, params: dict[str, object]) -> float:
    base_raw = params.get("base", 0.01)
    pred = float(base_raw) if isinstance(base_raw, (int, float, str)) else 0.01

    lr_raw = params.get("lr", 0.45)
    lr = float(lr_raw) if isinstance(lr_raw, (int, float, str)) else 0.45

    stumps_raw = params.get("stumps", [])
    stumps = stumps_raw if isinstance(stumps_raw, list) else []
    for stump in stumps:
        if not isinstance(stump, dict):
            continue
        feat = str(stump.get("feature", ""))
        thr = float(stump.get("threshold", 0.0))
        left_val = float(stump.get("left", 0.0))
        right_val = float(stump.get("right", 0.0))
        pred += lr * (left_val if float(item.features.get(feat, 0.0)) <= thr else right_val)
    return _clamp_delta(pred)


def _family_model(
    family: str,
    train_rows: list[_PreparedRow],
    target_key: str,
    training_mode: str,
) -> tuple[dict[str, object], Callable[[_PreparedRow], float]]:
    if family == "evt_changepoint_hybrid":
        if training_mode == "retrain":
            folds = _expanding_folds(train_rows, folds=3)
            candidates = [2, 3, 4]
            best_bins = 3
            best_score = float("inf")
            if folds:
                for bins in candidates:
                    score = 0.0
                    for tr, va in folds:
                        params = _fit_evt(tr, target_key=target_key, bins=bins)
                        errs = [abs(_predict_evt(item, params) - float(getattr(item, target_key))) for item in va]
                        score += _mean(errs)
                    score /= len(folds)
                    if score < best_score:
                        best_score = score
                        best_bins = bins
            params = _fit_evt(train_rows, target_key=target_key, bins=best_bins)
            params["cv"] = {"enabled": bool(folds), "folds": len(folds), "best_bins": best_bins}
        else:
            params = _fit_evt(train_rows, target_key=target_key, bins=3)
            params["cv"] = {"enabled": False, "folds": 0}
        return params, lambda item: _predict_evt(item, params)

    if family == "xgboost":
        feature_names = FEATURES_BY_FAMILY["xgboost"]
        if training_mode == "retrain":
            folds = _expanding_folds(train_rows, folds=3)
            grid: list[tuple[int, float]] = [(6, 0.45), (8, 0.35), (10, 0.25)]
            xgb_best_cfg = grid[0]
            best_score = float("inf")
            if folds:
                for rounds, lr in grid:
                    score = 0.0
                    for tr, va in folds:
                        params = _fit_boosted_stumps(tr, feature_names, target_key, rounds=rounds, lr=lr)
                        errs = [abs(_predict_boosted_stumps(item, params) - float(getattr(item, target_key))) for item in va]
                        score += _mean(errs)
                    score /= len(folds)
                    if score < best_score:
                        best_score = score
                        xgb_best_cfg = (rounds, lr)
            rounds, lr = xgb_best_cfg
            params = _fit_boosted_stumps(train_rows, feature_names, target_key, rounds=rounds, lr=lr)
            params["cv"] = {"enabled": bool(folds), "folds": len(folds), "best_rounds": rounds, "best_lr": lr}
        else:
            params = _fit_boosted_stumps(train_rows, feature_names, target_key, rounds=6, lr=0.45)
            params["cv"] = {"enabled": False, "folds": 0}
        return params, lambda item: _predict_boosted_stumps(item, params)

    # qenet + lstm_sequence use linear trainer with different feature space
    feature_key = "lstm_sequence" if family == "lstm_sequence" else "qenet"
    feature_names = FEATURES_BY_FAMILY[feature_key]
    if training_mode == "retrain":
        folds = _expanding_folds(train_rows, folds=3)
        linear_grid: list[tuple[float, float]] = [(0.005, 0.03), (0.01, 0.02), (0.05, 0.015)]
        linear_best_cfg = linear_grid[0]
        best_score = float("inf")
        if folds:
            for l2, lr in linear_grid:
                score = 0.0
                for tr, va in folds:
                    w, b = _linear_fit(tr, feature_names, target_key, l2=l2, lr=lr, epochs=140)
                    errs = [abs(_predict_linear(item, w, b, feature_names) - float(getattr(item, target_key))) for item in va]
                    score += _mean(errs)
                score /= len(folds)
                if score < best_score:
                    best_score = score
                    linear_best_cfg = (l2, lr)
        l2, lr = linear_best_cfg
        w, b = _linear_fit(train_rows, feature_names, target_key, l2=l2, lr=lr, epochs=160)
        params = {"weights": w, "bias": b, "features": list(feature_names), "l2": l2, "lr": lr, "cv": {"enabled": bool(folds), "folds": len(folds)}}
    else:
        w, b = _linear_fit(train_rows, feature_names, target_key, l2=0.01, lr=0.02, epochs=120)
        params = {"weights": w, "bias": b, "features": list(feature_names), "l2": 0.01, "lr": 0.02, "cv": {"enabled": False, "folds": 0}}
    weights_raw = params.get("weights", {})
    weights: dict[str, float] = {}
    if isinstance(weights_raw, dict):
        for k, v in weights_raw.items():
            if isinstance(k, str) and isinstance(v, (int, float, str)):
                weights[k] = float(v)

    bias_raw = params.get("bias", 0.0)
    bias = float(bias_raw) if isinstance(bias_raw, (int, float, str)) else 0.0
    return params, lambda item: _predict_linear(item, weights, bias, feature_names)


def _evaluate_predictions(test_rows: list[_PreparedRow], pred_floor_delta: list[float], pred_ceiling_delta: list[float]) -> dict[str, float]:
    floor_errors: list[float] = []
    ceiling_errors: list[float] = []
    spread_errors: list[float] = []
    for item, floor_delta, ceiling_delta in zip(test_rows, pred_floor_delta, pred_ceiling_delta):
        pred_floor = item.close * (1.0 - floor_delta)
        pred_ceiling = item.close * (1.0 + ceiling_delta)
        true_floor = item.close * (1.0 - item.floor_delta)
        true_ceiling = item.close * (1.0 + item.ceiling_delta)
        floor_errors.append(abs(pred_floor - true_floor))
        ceiling_errors.append(abs(pred_ceiling - true_ceiling))
        spread_errors.append(abs((pred_ceiling - pred_floor) - (true_ceiling - true_floor)))

    denom_floor = max(1, len(floor_errors))
    denom_ceiling = max(1, len(ceiling_errors))
    denom_spread = max(1, len(spread_errors))
    return {
        "mae_floor": sum(floor_errors) / denom_floor,
        "mae_ceiling": sum(ceiling_errors) / denom_ceiling,
        "mae_spread": sum(spread_errors) / denom_spread,
        "test_floor_coverage": len(floor_errors) / max(1, len(test_rows)),
        "test_ceiling_coverage": len(ceiling_errors) / max(1, len(test_rows)),
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
    train_rows_raw, test_rows_raw = _split(rows)

    candidates: list[HorizonCompetitionCandidate] = []
    horizon_specs = [s for s in build_model_specs() if s.horizon == horizon]
    family_features = sorted({name for family in FEATURES_BY_FAMILY.values() for name in family})
    train_prepared = _prepare_rows(train_rows_raw, floor_col, ceiling_col, tuple(family_features))
    test_prepared = _prepare_rows(test_rows_raw, floor_col, ceiling_col, tuple(family_features))

    if not train_prepared:
        raise ValueError(f"No training rows with valid labels for horizon={horizon}")
    if not test_prepared:
        test_prepared = train_prepared[-max(1, len(train_prepared) // 3) :]

    for spec in horizon_specs:
        family = spec.model_family
        floor_params, floor_fn = _family_model(family, train_prepared, target_key="floor_delta", training_mode=training_mode)
        ceil_params, ceil_fn = _family_model(family, train_prepared, target_key="ceiling_delta", training_mode=training_mode)

        pred_floor_delta = [floor_fn(item) for item in test_prepared]
        pred_ceiling_delta = [ceil_fn(item) for item in test_prepared]
        metrics = _evaluate_predictions(test_prepared, pred_floor_delta, pred_ceiling_delta)

        floor_delta = _quantiles(pred_floor_delta, 0.5)
        ceiling_delta = _quantiles(pred_ceiling_delta, 0.5)

        candidates.append(
            HorizonCompetitionCandidate(
                model_id=spec.model_id,
                model_family=family,
                horizon=horizon,
                version=version,
                floor_delta=float(_clamp_delta(floor_delta)),
                ceiling_delta=float(_clamp_delta(ceiling_delta)),
                train_rows=len(train_prepared),
                test_rows=len(test_prepared),
                metrics=metrics,
                params={"floor": floor_params, "ceiling": ceil_params, "training_mode": training_mode},
            )
        )

    champion = min(candidates, key=lambda c: (c.metrics["mae_spread"], c.metrics["mae_floor"] + c.metrics["mae_ceiling"]))
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

    selected_horizons = list(HORIZONS if not tasks else [h for h in tasks if h in HORIZONS])
    if not selected_horizons:
        raise ValueError("No valid horizon tasks requested. Use one or more of: d1,w1,q1")

    artifacts: list[HorizonBaselineArtifact] = []
    for horizon in selected_horizons:
        candidates, champion_candidate = train_horizon_competition(rows, horizon=horizon, version=version, training_mode=training_mode)
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
        out_path = output_dir / f"{horizon}_champion.json"
        out_path.write_text(json.dumps(asdict(artifact), ensure_ascii=False, indent=2), encoding="utf-8")
        competition_out = output_dir / f"{horizon}_competition.json"
        competition_out.write_text(
            json.dumps(
                {
                    "horizon": horizon,
                    "version": version,
                    "selection_metric": "mae_spread_then_total_mae",
                    "training_mode": training_mode,
                    "selected_model_id": champion_candidate.model_id,
                    "candidates": [asdict(c) for c in candidates],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        logger.info(
            "[horizon-training] horizon=%s champion=%s train_rows=%s test_rows=%s mae_floor=%.6f mae_ceiling=%.6f mode=%s",
            horizon,
            artifact.model_name,
            artifact.train_rows,
            artifact.test_rows,
            artifact.metrics["mae_floor"],
            artifact.metrics["mae_ceiling"],
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
            ],
        )
        writer.writeheader()
        for artifact in artifacts:
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
                }
            )

    logger.info("[horizon-training] wrote csv summary=%s", csv_path)
    return csv_path


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    parser = argparse.ArgumentParser(description="Train d1/w1/q1 horizon champions (evt/xgboost/lstm/qenet) and export CSV summary")
    parser.add_argument("--dataset", required=True, help="Path to modelable_dataset.json")
    parser.add_argument("--output-dir", default="data/training/models", help="Directory for champion JSON and CSV report")
    parser.add_argument("--version", required=True, help="Version tag")
    parser.add_argument("--tasks", default="d1,w1,q1", help="Comma-separated horizons to train")
    parser.add_argument("--training-mode", default="standard", choices=["standard", "manual", "retrain", "renewal"], help="Training mode")
    args = parser.parse_args()

    tasks = tuple(part.strip() for part in str(args.tasks).split(",") if part.strip())
    run(Path(args.dataset), Path(args.output_dir), args.version, tasks=tasks, training_mode=args.training_mode)


if __name__ == "__main__":
    main()
