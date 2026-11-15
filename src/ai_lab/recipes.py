"""Registered recipes reuse FLOOR's purged time splitter; no generated code."""

import math
from datetime import datetime
import numpy as np
from sklearn.dummy import DummyRegressor
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from models.temporal_cv import purged_expanding_folds


def instant(value):
    d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if d.tzinfo is None:
        raise ValueError("TIMEZONE_REQUIRED")
    return d


def validate_rows(rows, manifest, plan):
    if not 100 <= len(rows) <= 10000:
        raise ValueError("DATASET_SIZE_OUT_OF_POLICY")
    if manifest.get("target") != "floor_w1" or manifest.get("horizon_sessions") != 5:
        raise ValueError("TARGET_CONTRACT_MISMATCH")
    if manifest.get("temporal_evidence") not in (
        "synthetic_fixture",
        "retrospective_reconstructed",
        "verified_vintage",
    ):
        raise ValueError("TEMPORAL_EVIDENCE_REQUIRED")
    if not set(plan.features) <= set(manifest.get("features", [])):
        raise ValueError("FEATURE_NOT_REGISTERED")
    seen = set()
    for r in rows:
        ts, end = instant(r["timestamp"]), instant(r["target_end_date_w1"])
        if end <= ts or r.get("horizon_complete_w1") is not True:
            raise ValueError("INCOMPLETE_OR_INVALID_LABEL")
        if instant(r["available_at"]) > ts:
            raise ValueError("FUTURE_INFORMATION_BLOCKED")
        if not set(plan.features) <= set(r.get("feature_available_at", {})):
            raise ValueError("FEATURE_AVAILABILITY_UNKNOWN")
        if any(instant(r["feature_available_at"][f]) > ts for f in plan.features):
            raise ValueError("FEATURE_LEAKAGE_BLOCKED")
        if (r["symbol"], r["timestamp"]) in seen:
            raise ValueError("DUPLICATE_OBSERVATION")
        seen.add((r["symbol"], r["timestamp"]))
        if not math.isfinite(float(r["floor_w1"])) or not float(r["close"]) > 0:
            raise ValueError("INVALID_TARGET_OR_PRICE")
        for f in plan.features:
            if r.get(f) is not None and not math.isfinite(float(r[f])):
                raise ValueError("NONFINITE_FEATURE")
    if not purged_expanding_folds(
        rows, target_end_field="target_end_date_w1", folds=plan.folds
    ):
        raise ValueError("INSUFFICIENT_PURGED_HISTORY")


def estimator(name, seed):
    models = {
        "dummy": lambda: DummyRegressor(strategy="mean"),
        "ridge": lambda: Ridge(alpha=1.0),
        "extra_trees": lambda: ExtraTreesRegressor(
            n_estimators=32,
            max_depth=5,
            min_samples_leaf=10,
            random_state=seed,
            n_jobs=1,
        ),
    }
    return make_pipeline(
        SimpleImputer(strategy="median", keep_empty_features=True),
        StandardScaler(),
        models[name](),
    )


def matrix(rows, features):
    return np.array(
        [
            [float(r[f]) if r.get(f) is not None else np.nan for f in features]
            for r in rows
        ]
    )


def target(rows):
    return np.array(
        [(float(r["close"]) - float(r["floor_w1"])) / float(r["close"]) for r in rows]
    )


def metrics(y, p):
    errors = y - p
    return {
        "mae_floor_pct": float(np.abs(errors).mean()),
        "rmse_floor_pct": float(np.sqrt(np.square(errors).mean())),
    }


def evaluate(rows, manifest, plan):
    validate_rows(rows, manifest, plan)
    if plan.recipe == "strategy_comparison":
        raise ValueError("STRATEGY_REQUIRES_ISOLATED_ARTIFACT_DIRECTORY")
    if plan.recipe == "frozen_model_comparison":
        from ai_lab.frozen_models import evaluate_frozen

        return evaluate_frozen(rows, manifest, plan)
    folds = purged_expanding_folds(
        rows, target_end_field="target_end_date_w1", folds=plan.folds
    )
    if len(folds) != plan.folds:
        raise ValueError("REQUESTED_FOLDS_UNAVAILABLE")
    variants = [("full", plan.features)]
    if plan.recipe == "feature_ablation":
        variants.append(
            ("ablated", [f for f in plan.features if f not in plan.drop_features])
        )
    all_scores, predictions, fit_costs = [], [], []
    import time

    for variant, features in variants:
        # Baseline always present, even if omitted by the caller.
        for model in sorted(set(plan.models) | {"dummy"}):
            for i, (train, valid) in enumerate(folds):
                x, v = matrix(train, features), matrix(valid, features)
                y, z = target(train), target(valid)
                fitted = estimator(model, plan.seed)
                start = time.monotonic()
                fitted.fit(x, y)
                fit_seconds = time.monotonic() - start
                start = time.monotonic()
                pred = fitted.predict(v)
                infer_seconds = time.monotonic() - start
                fit_costs.append(
                    {
                        "model": model,
                        "variant": variant,
                        "fold": i,
                        "fit_seconds": fit_seconds,
                        "inference_seconds": infer_seconds,
                    }
                )
                all_scores.append(
                    {
                        "variant": variant,
                        "model": model,
                        "fold": i,
                        "train": metrics(y, fitted.predict(x)),
                        "oos": metrics(z, pred),
                        "train_rows": len(train),
                        "oos_rows": len(valid),
                        "train_end": max(r["target_end_date_w1"] for r in train),
                        "test_start": min(r["timestamp"] for r in valid),
                        "test_end": max(r["timestamp"] for r in valid),
                    }
                )
                for r, observed, prediction in zip(valid, z, pred):
                    predictions.append(
                        {
                            "variant": variant,
                            "model": model,
                            "fold": i,
                            "timestamp": r["timestamp"],
                            "symbol": r["symbol"],
                            "actual": float(observed),
                            "prediction": float(prediction),
                        }
                    )
    summary = []
    for variant, _ in variants:
        for model in sorted(set(plan.models) | {"dummy"}):
            records = [
                r
                for r in predictions
                if r["model"] == model and r["variant"] == variant
            ]
            score = metrics(
                np.array([r["actual"] for r in records]),
                np.array([r["prediction"] for r in records]),
            )
            fold_scores = [
                r["oos"]["mae_floor_pct"]
                for r in all_scores
                if r["model"] == model and r["variant"] == variant
            ]
            by_symbol = {}
            for symbol in sorted({r["symbol"] for r in records}):
                subset = [r for r in records if r["symbol"] == symbol]
                by_symbol[symbol] = metrics(
                    np.array([r["actual"] for r in subset]),
                    np.array([r["prediction"] for r in subset]),
                )
            summary.append(
                {
                    "model": model,
                    "variant": variant,
                    **score,
                    "fold_mae_std": float(np.std(fold_scores)),
                    "sample_size": len(records),
                    "by_symbol": by_symbol,
                }
            )
    feature_report = []
    if plan.recipe == "feature_audit":
        # Preregistered Ridge on every fold, never selected using test scores.
        importance = {f: [] for f in plan.features}
        for train, valid in folds:
            x, v = matrix(train, plan.features), matrix(valid, plan.features)
            y, z = target(train), target(valid)
            fitted = estimator("ridge", plan.seed).fit(x, y)
            baseline = metrics(z, fitted.predict(v))["mae_floor_pct"]
            rng = np.random.default_rng(plan.seed)
            for j, f in enumerate(plan.features):
                deltas = []
                for _ in range(5):
                    permuted = v.copy()
                    permuted[:, j] = rng.permutation(permuted[:, j])
                    deltas.append(
                        metrics(z, fitted.predict(permuted))["mae_floor_pct"] - baseline
                    )
                importance[f].append(float(np.mean(deltas)))
        from scipy.stats import ks_2samp

        train, valid = folds[-1]
        train_x, valid_x = matrix(train, plan.features), matrix(valid, plan.features)
        all_x = matrix(rows, plan.features)
        for j, f in enumerate(plan.features):
            values = all_x[:, j]
            finite = values[np.isfinite(values)]
            constant = len(np.unique(finite)) <= 1
            correlations = []
            for k, other in enumerate(plan.features):
                if j == k:
                    continue
                mask = np.isfinite(values) & np.isfinite(all_x[:, k])
                if (
                    mask.sum() >= 10
                    and np.std(values[mask]) > 0
                    and np.std(all_x[mask, k]) > 0
                ):
                    rho = float(np.corrcoef(values[mask], all_x[mask, k])[0, 1])
                    if abs(rho) >= 0.9:
                        correlations.append({"feature": other, "correlation": rho})
            tr = train_x[:, j][np.isfinite(train_x[:, j])]
            va = valid_x[:, j][np.isfinite(valid_x[:, j])]
            q1, q3 = np.quantile(tr, [0.25, 0.75]) if len(tr) else (0.0, 0.0)
            outside = (
                float(
                    np.mean((va < q1 - 1.5 * (q3 - q1)) | (va > q3 + 1.5 * (q3 - q1)))
                )
                if len(va)
                else None
            )
            feature_report.append(
                {
                    "feature": f,
                    "missing_ratio": float(np.isnan(values).mean()),
                    "constant": constant,
                    "high_correlations": correlations,
                    "validation_outside_training_iqr_fences_ratio": outside,
                    "ks_distribution_distance": float(ks_2samp(tr, va).statistic)
                    if len(tr) and len(va)
                    else None,
                    "permutation_mae_increase_by_fold": importance[f],
                    "permutation_mae_increase": float(np.mean(importance[f])),
                    "importance_fold_std": float(np.std(importance[f])),
                    "recommendation": "Eliminar como candidata"
                    if constant
                    else "Investigar",
                    "temporal_availability": "validated under declared manifest assumptions",
                    "evidence_scope": "fixed Ridge, all folds; permutation is exploratory under dependence, no automatic elimination",
                }
            )
    best = min(summary, key=lambda r: r[plan.objective])
    return {
        "schema_version": "1.0",
        "measured_by": "registered Python recipe",
        "dataset_id": plan.dataset_id,
        "temporal_evidence": manifest["temporal_evidence"],
        "comparability": {
            "same_rows": True,
            "same_folds": True,
            "same_target": True,
            "same_features_except_declared_ablation": True,
        },
        "metrics": summary,
        "folds": all_scores,
        "feature_audit": feature_report,
        "costs": fit_costs,
        "predictions": predictions,
        "ranking_leader": {
            "model": best["model"],
            "variant": best["variant"],
            "objective": plan.objective,
        },
        "conclusion": "Exploratory ranking only; no statistically established winner or production recommendation.",
        "limitations": [
            "Fixed candidate hyperparameters; no test-set tuning.",
            "Forward W1 labels overlap: fold dispersion is not a confidence interval.",
            "No calibrated intervals, regime inference or trading-return claims in this recipe.",
            "Reconstructed provider history is not historical vintage evidence.",
            "Synthetic fixtures validate software only, never market performance.",
        ],
    }
