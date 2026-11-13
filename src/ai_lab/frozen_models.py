"""Compare actual frozen FLOOR W1 heads; never train, mutate or promote them."""

from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import time
import numpy as np
from ai_lab.store import digest
from ai_lab.recipes import instant, metrics
from models.classic_horizon_predictor import (
    build_runtime_features,
    model_family,
    predict_family_delta,
    validate_family_params,
)
from models.robust_range_v3 import ROBUST_RANGE_FEATURES
from models.central_skill_ensemble import CENTRAL_SKILL_FEATURES

REGISTRY = Path(__file__).resolve().parents[2] / "data/training/models"
REQUIRED_INPUTS = tuple(
    sorted(set(ROBUST_RANGE_FEATURES) | set(CENTRAL_SKILL_FEATURES))
)


def catalog():
    records = []
    for path in sorted(REGISTRY.glob("w1_*.json")):
        if path.stem == "w1_competition":
            continue
        value = json.loads(path.read_text())
        name = str(value.get("model_name", ""))
        if value.get("horizon") != "w1" or not model_family(name):
            continue
        stamp = re.match(r"^(\d{8}T\d{6}Z)", str(value.get("version", "")))
        created = (
            datetime.strptime(stamp[1], "%Y%m%dT%H%M%SZ")
            .replace(tzinfo=timezone.utc)
            .isoformat()
            if stamp
            else None
        )
        records.append(
            {
                "id": path.stem,
                "model_name": name,
                "version": value.get("version"),
                "artifact_timestamp": created,
                "artifact_sha256": digest(path.read_bytes()),
                "bytes": path.stat().st_size,
                "training_cutoff_verified": False,
                "stored_metrics_scope": "historical, not comparable without re-evaluation",
                "status": "EXPERIMENTAL_REPLAY_ONLY"
                if created
                else "BLOCKED_TIMESTAMP_UNKNOWN",
            }
        )
    return records


def resolve(model_ids):
    available = {r["id"]: r for r in catalog()}
    if len(set(model_ids)) != len(model_ids):
        raise ValueError("DUPLICATE_FROZEN_MODELS")
    resolved = []
    for model_id in model_ids:
        if model_id not in available:
            raise ValueError("FROZEN_MODEL_NOT_REGISTERED")
        record = available[model_id]
        if record["artifact_timestamp"] is None:
            raise ValueError("MODEL_TIMESTAMP_UNKNOWN")
        path = REGISTRY / (
            model_id + ".json"
        )  # model_id first resolved against allowlist, never a client path
        artifact = json.loads(path.read_text())
        family = model_family(artifact["model_name"])
        if family not in ("central_skill_ensemble_v1", "robust_range_v3"):
            raise ValueError("FROZEN_FAMILY_NOT_REVIEWED")
        params = artifact.get("params", {})
        if params.get("schema_version") != 2:
            raise ValueError("MODEL_SCHEMA_UNSUPPORTED")
        for head in ("floor", "ceiling"):
            validate_family_params(family, params.get(head, {}))
        resolved.append((record, artifact))
    return resolved


def validate_frozen_inputs(rows, manifest, plan):
    models = resolve(plan.frozen_model_ids)
    if not set(REQUIRED_INPUTS) <= set(manifest.get("materialized_features", [])):
        raise ValueError("FROZEN_INPUT_MATERIALIZATION_REQUIRED")
    cutoff = max(instant(m["artifact_timestamp"]) for m, _ in models)
    holdout = [r for r in rows if instant(r["timestamp"]) > cutoff]
    if len(holdout) < 30 or len({r["timestamp"] for r in holdout}) < 10:
        raise ValueError("INSUFFICIENT_POST_ARTIFACT_HISTORY")
    for r in holdout:
        if not math.isfinite(float(r.get("ceiling_w1", float("nan")))):
            raise ValueError("CEILING_TARGET_REQUIRED")
        for feature in REQUIRED_INPUTS:
            if (
                feature not in r
                or r[feature] is None
                or not math.isfinite(float(r[feature]))
            ):
                raise ValueError("FROZEN_REQUIRED_FEATURE_MISSING")
            available = r.get("feature_available_at", {}).get(feature)
            if available is None or instant(available) > instant(r["timestamp"]):
                raise ValueError("FROZEN_FEATURE_AVAILABILITY_INVALID")
    if (
        manifest.get("feature_definition_version")
        != "floor-feature-builder/full-v2-benchmark-joined"
    ):
        raise ValueError("FROZEN_FEATURE_CONTRACT_MISMATCH")
    return models, cutoff, sorted(holdout, key=lambda r: (r["timestamp"], r["symbol"]))


def evaluate_frozen(rows, manifest, plan):
    models, cutoff, holdout = validate_frozen_inputs(rows, manifest, plan)
    dates = sorted({r["timestamp"] for r in holdout})
    date_blocks = np.array_split(np.array(dates), plan.folds)
    block_of = {str(d): i for i, block in enumerate(date_blocks) for d in block}
    prior = [r for r in rows if instant(r["target_end_date_w1"]) < cutoff]
    if len(prior) < 30:
        raise ValueError("INSUFFICIENT_BASELINE_PRE_CUTOFF_DATA")
    dummy_floor = float(
        np.mean([(r["close"] - r["floor_w1"]) / r["close"] for r in prior])
    )
    dummy_ceiling = float(
        np.mean([(r["ceiling_w1"] - r["close"]) / r["close"] for r in prior])
    )
    y = np.array([(r["close"] - r["floor_w1"]) / r["close"] for r in holdout])
    z = np.array([(r["ceiling_w1"] - r["close"]) / r["close"] for r in holdout])
    summary, predictions, folds, costs = [], [], [], []
    candidates = [(r["id"], r, a) for r, a in models] + [
        ("dummy_pre_cutoff", None, None),
        ("atr_sqrt5", None, None),
    ]
    for model_id, record, artifact in candidates:
        floor_predictions = []
        ceiling_predictions = []
        risks = []
        started = time.perf_counter()
        for row in holdout:
            risk_floor = risk_ceiling = None
            if artifact is not None:
                family = model_family(artifact["model_name"])
                params = artifact["params"]
                features = build_runtime_features(row)
                floor_delta = predict_family_delta(
                    family, params["floor"], features, validate=False
                )
                ceiling_delta = predict_family_delta(
                    family, params["ceiling"], features, validate=False
                )
                risk = params.get("risk_geometry", {})
                if risk.get("method") in (
                    "validation_residual_quantile",
                    "joint_validation_conformal_max_residual",
                ):
                    risk_floor = min(
                        0.7,
                        max(
                            0.0001,
                            floor_delta
                            + max(0.0, float(risk.get("floor_delta_addon", 0.0))),
                        ),
                    )
                    risk_ceiling = min(
                        0.7,
                        max(
                            0.0001,
                            ceiling_delta
                            + max(0.0, float(risk.get("ceiling_delta_addon", 0.0))),
                        ),
                    )
            elif model_id == "dummy_pre_cutoff":
                floor_delta, ceiling_delta = dummy_floor, dummy_ceiling
            else:
                floor_delta = ceiling_delta = (
                    float(row["atr_14"]) / float(row["close"]) * math.sqrt(5)
                )
            floor_predictions.append(floor_delta)
            ceiling_predictions.append(ceiling_delta)
            risks.append((risk_floor, risk_ceiling))
        seconds = time.perf_counter() - started
        p, q = np.array(floor_predictions), np.array(ceiling_predictions)
        per_block = []
        for i in range(plan.folds):
            mask = np.array([block_of[r["timestamp"]] == i for r in holdout])
            score = metrics(y[mask], p[mask])
            per_block.append(score["mae_floor_pct"])
            folds.append(
                {
                    "model": model_id,
                    "variant": "frozen",
                    "fold": i,
                    "train": None,
                    "oos_verified": False,
                    "post_artifact_holdout": score,
                    "rows": int(mask.sum()),
                    "start": str(date_blocks[i][0]),
                    "end": str(date_blocks[i][-1]),
                }
            )
        risk_available = all(a is not None and b is not None for a, b in risks)
        joint = None
        width = None
        if risk_available:
            down = np.array([a for a, _ in risks], dtype=float)
            up = np.array([b for _, b in risks], dtype=float)
            joint = float(np.mean((y <= down) & (z <= up)))
            width = float(np.mean(down + up))
        score = {
            "model": model_id,
            "variant": "frozen",
            **metrics(y, p),
            "mae_ceiling_pct": float(np.mean(np.abs(z - q))),
            "central_joint_coverage": float(np.mean((y <= p) & (z <= q))),
            "risk_joint_coverage": joint,
            "risk_mean_width_pct": width,
            "fold_mae_std": float(np.std(per_block)),
            "sample_size": len(holdout),
            "by_symbol": {},
        }
        for symbol in sorted({r["symbol"] for r in holdout}):
            mask = np.array([r["symbol"] == symbol for r in holdout])
            score["by_symbol"][symbol] = metrics(y[mask], p[mask])
        summary.append(score)
        costs.append(
            {
                "model": model_id,
                "fit_seconds": 0,
                "inference_seconds": seconds,
                "artifact_bytes": record["bytes"] if record else 0,
            }
        )
        for row, actual, fc, cc, (rf, rc) in zip(holdout, y, p, q, risks):
            predictions.append(
                {
                    "model": model_id,
                    "variant": "frozen",
                    "timestamp": row["timestamp"],
                    "symbol": row["symbol"],
                    "actual": float(actual),
                    "prediction": float(fc),
                    "predicted_ceiling_delta": float(cc),
                    "risk_floor_delta": rf,
                    "risk_ceiling_delta": rc,
                    "fold": block_of[row["timestamp"]],
                }
            )
    best = min(summary, key=lambda m: m[plan.objective])
    return {
        "schema_version": "1.1",
        "measured_by": "exact FLOOR serialized head inference",
        "dataset_id": plan.dataset_id,
        "temporal_evidence": manifest["temporal_evidence"],
        "comparability": {
            "same_rows": True,
            "same_target": True,
            "same_evaluation_window": True,
            "same_input_materialization": True,
            "native_model_feature_contracts_may_differ": True,
            "cutoff": cutoff.isoformat(),
            "cutoff_basis": "latest parsed artifact version timestamp, not verified training boundary",
            "out_of_sample_verified": False,
            "evidence_type": "post_artifact_reconstructed_holdout",
            "start": holdout[0]["timestamp"],
            "end": holdout[-1]["timestamp"],
            "observation_dates": len(dates),
        },
        "model_assets": [r for r, _ in models],
        "metrics": summary,
        "folds": folds,
        "feature_audit": [],
        "costs": costs,
        "predictions": predictions,
        "ranking_leader": {
            "model": best["model"],
            "variant": "frozen",
            "objective": plan.objective,
        },
        "conclusion": "Measured ranking of frozen FLOOR heads on common post-artifact reconstructed observations; OOS status unverified, no promotion or statistically established winner.",
        "limitations": [
            "Model artifact timestamp is not a verified training/selection cutoff.",
            "Historical AI inputs unavailable: exact serving fallback/imputation is an explicit replay assumption.",
            "Provider history may have been revised; no vintage PIT/prospective evidence.",
            "Daily-bar reconstruction and survivor universe only; short, overlapping W1 observations.",
            "Temporal blocks summarize stability; no retraining, interval recalibration or test tuning.",
        ],
    }
