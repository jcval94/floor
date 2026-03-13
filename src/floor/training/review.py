from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from floor.storage import append_jsonl
from models.dataset_summary import summarize_modelable_rows
from models.inference import format_champion_version, predict_timing_week_probabilities, predict_value_floor_m3
from monitoring.drift_detection import js_divergence
from monitoring.run_retrain_assessment import load_simple_yaml
from models.evaluate import timing_metrics, value_metrics

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
MODEL_KEYS = ("value", "timing")
MODEL_DEFAULTS = {
    "value": {"model_name": "m3_value_linear", "champion": Path("data/training/models/value_champion.json")},
    "timing": {"model_name": "m3_timing_multiclass", "champion": Path("data/training/models/timing_champion.json")},
}


def _load_dataset_rows(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        return payload.get("rows", [])
    if isinstance(payload, list):
        return payload
    raise ValueError(f"Unsupported dataset payload: {path}")


def _split_eval_rows(rows: list[dict]) -> list[dict]:
    eval_rows = [row for row in rows if row.get("split") in {"validation", "test"}]
    if eval_rows:
        return eval_rows
    start = int(len(rows) * 0.7)
    return rows[start:]


def _state_rank(state: str) -> int:
    return {"GREEN": 0, "YELLOW": 1, "RED": 2}.get(state, 0)


def _worst_state(states: list[str]) -> str:
    return max(states, key=_state_rank) if states else "GREEN"


def _status_from_state(state: str) -> str:
    return {"GREEN": "OK", "YELLOW": "WARN", "RED": "ALERT"}.get(state, "OK")


def _recommendation_from_state(state: str) -> str:
    return {"GREEN": "SKIP_RETRAIN", "YELLOW": "RETRAIN_SOON", "RED": "RETRAIN_NOW"}.get(state, "SKIP_RETRAIN")


def _score_state(score: float, warn: float, fail: float) -> str:
    if score >= fail:
        return "RED"
    if score >= warn:
        return "YELLOW"
    return "GREEN"


def _relative_shift(ref_value: float | None, cur_value: float | None, ref_std: float | None = None) -> float:
    if ref_value is None or cur_value is None:
        return 0.0
    scale = max(abs(ref_value), abs(ref_std or 0.0), 1e-6)
    return abs(cur_value - ref_value) / scale


def _read_artifact(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _feature_drift(reference_summary: dict, current_summary: dict, cfg: dict) -> dict:
    important_features = [item.strip() for item in str(cfg["important_features"]["columns"]).split(",") if item.strip()]
    ref_stats = reference_summary.get("numeric_stats", {})
    cur_stats = current_summary.get("numeric_stats", {})
    per_feature: dict[str, dict] = {}
    scores: list[float] = []
    states: list[str] = []
    for feature in important_features:
        ref = ref_stats.get(feature, {})
        cur = cur_stats.get(feature, {})
        score = _relative_shift(ref.get("mean"), cur.get("mean"), ref.get("std"))
        state = _score_state(score, float(cfg["thresholds"]["feature_psi_warn"]), float(cfg["thresholds"]["feature_psi_fail"]))
        per_feature[feature] = {"score": score, "state": state}
        scores.append(score)
        states.append(state)
    return {
        "state": _worst_state(states),
        "score": max(scores) if scores else 0.0,
        "features": per_feature,
    }


def _schema_drift(reference_summary: dict, current_summary: dict, cfg: dict) -> dict:
    ref_cols = set(reference_summary.get("columns", []))
    cur_cols = set(current_summary.get("columns", []))
    removed = sorted(ref_cols - cur_cols)
    added = sorted(cur_cols - ref_cols)
    min_coverage = float(cfg["thresholds"]["min_column_coverage"])
    low_coverage = sorted(
        column
        for column in ref_cols
        if float(current_summary.get("coverage_by_column", {}).get(column, 0.0)) < min_coverage
    )

    state = "GREEN"
    if removed or low_coverage:
        state = "RED"
    elif added:
        state = "YELLOW"

    coverage_gap = 0.0
    for column in ref_cols:
        ref_cov = float(reference_summary.get("coverage_by_column", {}).get(column, 0.0))
        cur_cov = float(current_summary.get("coverage_by_column", {}).get(column, 0.0))
        coverage_gap = max(coverage_gap, max(ref_cov - cur_cov, 0.0))

    return {
        "state": state,
        "score": coverage_gap,
        "removed_columns": removed,
        "added_columns": added,
        "low_coverage_columns": low_coverage,
    }


def _value_target_drift(reference_summary: dict, current_summary: dict, cfg: dict) -> dict:
    ref_stats = reference_summary.get("numeric_stats", {})
    cur_stats = current_summary.get("numeric_stats", {})
    scores = {}
    for column in ["floor_m3", "realized_floor_m3"]:
        ref = ref_stats.get(column, {})
        cur = cur_stats.get(column, {})
        scores[column] = _relative_shift(ref.get("mean"), cur.get("mean"), ref.get("std"))
    score = max(scores.values()) if scores else 0.0
    state = _score_state(
        score,
        float(cfg["m3_drift_thresholds"]["realized_floor_m3_js_warn"]),
        float(cfg["m3_drift_thresholds"]["realized_floor_m3_js_fail"]),
    )
    return {"state": state, "score": score, "targets": scores}


def _timing_target_drift(reference_summary: dict, current_summary: dict, cfg: dict) -> dict:
    ref_counts = reference_summary.get("categorical_counts", {}).get("floor_week_m3", {})
    cur_counts = current_summary.get("categorical_counts", {}).get("floor_week_m3", {})
    score = js_divergence(ref_counts, cur_counts)
    state = _score_state(
        score,
        float(cfg["m3_drift_thresholds"]["floor_week_m3_js_warn"]),
        float(cfg["m3_drift_thresholds"]["floor_week_m3_js_fail"]),
    )
    return {"state": state, "score": score, "targets": {"floor_week_m3": score}}


def _value_performance(artifact: dict, rows: list[dict], cfg: dict) -> dict:
    eval_rows = [row for row in rows if row.get("floor_m3") is not None]
    if not eval_rows:
        return {
            "state": "YELLOW",
            "score": 0.0,
            "current_metrics": {},
            "baseline_metrics": artifact.get("metrics", {}),
            "deltas": {"insufficient_rows": 1.0},
        }

    y_true = [float(row["floor_m3"]) for row in eval_rows]
    y_pred = [predict_value_floor_m3(row, artifact) for row in eval_rows]
    confidences = [0.5 + min(0.45, abs(float(row.get("ai_conviction_long") or 0.0) * 0.4)) for row in eval_rows]
    current_metrics = value_metrics(y_true, y_pred, confidences)
    baseline_metrics = artifact.get("metrics", {})

    deltas = {
        "pinball_loss": float(current_metrics.get("pinball_loss", 0.0)) - float(baseline_metrics.get("pinball_loss", 0.0)),
        "breach_rate": abs(float(current_metrics.get("breach_rate", 0.0)) - float(baseline_metrics.get("breach_rate", 0.0))),
        "calibration_error": abs(float(current_metrics.get("calibration_error", 0.0)) - float(baseline_metrics.get("calibration_error", 0.0))),
        "temporal_stability_drop": max(float(baseline_metrics.get("temporal_stability", 0.0)) - float(current_metrics.get("temporal_stability", 0.0)), 0.0),
    }

    warn = float(cfg["performance_thresholds"]["pinball_loss_warn"])
    fail = float(cfg["performance_thresholds"]["pinball_loss_fail"])
    calibration_warn = float(cfg["thresholds"]["coverage_calibration_warn"])
    calibration_fail = float(cfg["thresholds"]["coverage_calibration_fail"])
    state = "GREEN"
    if (
        deltas["pinball_loss"] >= fail
        or deltas["breach_rate"] >= float(cfg["performance_thresholds"]["breach_rate_fail"])
        or deltas["calibration_error"] >= calibration_fail
    ):
        state = "RED"
    elif (
        deltas["pinball_loss"] >= warn
        or deltas["breach_rate"] >= float(cfg["performance_thresholds"]["breach_rate_warn"])
        or deltas["calibration_error"] >= calibration_warn
        or deltas["temporal_stability_drop"] >= warn
    ):
        state = "YELLOW"

    return {
        "state": state,
        "score": max(deltas.values()) if deltas else 0.0,
        "current_metrics": current_metrics,
        "baseline_metrics": baseline_metrics,
        "deltas": deltas,
    }


def _timing_performance(artifact: dict, rows: list[dict], cfg: dict) -> dict:
    eval_rows = [row for row in rows if row.get("floor_week_m3") is not None]
    if not eval_rows:
        return {
            "state": "YELLOW",
            "score": 0.0,
            "current_metrics": {},
            "baseline_metrics": artifact.get("metrics", {}),
            "deltas": {"insufficient_rows": 1.0},
        }

    y_true = [int(row["floor_week_m3"]) for row in eval_rows]
    probs = [predict_timing_week_probabilities(row, artifact) for row in eval_rows]
    current_metrics = timing_metrics(y_true, probs)
    baseline_metrics = artifact.get("metrics", {})

    deltas = {
        "top1_accuracy_drop": max(float(baseline_metrics.get("top1_accuracy", 0.0)) - float(current_metrics.get("top1_accuracy", 0.0)), 0.0),
        "top3_accuracy_drop": max(float(baseline_metrics.get("top3_accuracy", 0.0)) - float(current_metrics.get("top3_accuracy", 0.0)), 0.0),
        "log_loss": float(current_metrics.get("log_loss", 0.0)) - float(baseline_metrics.get("log_loss", 0.0)),
        "brier_score": float(current_metrics.get("brier_score", 0.0)) - float(baseline_metrics.get("brier_score", 0.0)),
        "expected_week_distance": float(current_metrics.get("expected_week_distance", 0.0)) - float(baseline_metrics.get("expected_week_distance", 0.0)),
    }

    state = "GREEN"
    if (
        deltas["top1_accuracy_drop"] >= float(cfg["m3_performance_thresholds"]["top1_accuracy_m3_drop_fail"])
        or deltas["top3_accuracy_drop"] >= float(cfg["m3_performance_thresholds"]["top3_accuracy_m3_drop_fail"])
        or deltas["log_loss"] >= float(cfg["timing_thresholds"]["log_loss_fail"])
        or deltas["brier_score"] >= float(cfg["timing_thresholds"]["brier_fail"])
        or deltas["expected_week_distance"] >= float(cfg["m3_performance_thresholds"]["week_distance_m3_fail"])
    ):
        state = "RED"
    elif (
        deltas["top1_accuracy_drop"] >= float(cfg["m3_performance_thresholds"]["top1_accuracy_m3_drop_warn"])
        or deltas["top3_accuracy_drop"] >= float(cfg["m3_performance_thresholds"]["top3_accuracy_m3_drop_warn"])
        or deltas["log_loss"] >= float(cfg["timing_thresholds"]["log_loss_warn"])
        or deltas["brier_score"] >= float(cfg["timing_thresholds"]["brier_warn"])
        or deltas["expected_week_distance"] >= float(cfg["m3_performance_thresholds"]["week_distance_m3_warn"])
    ):
        state = "YELLOW"

    return {
        "state": state,
        "score": max(deltas.values()) if deltas else 0.0,
        "current_metrics": current_metrics,
        "baseline_metrics": baseline_metrics,
        "deltas": deltas,
    }


def _build_record(model_key: str, artifact: dict | None, current_rows: list[dict], current_summary: dict, cfg: dict) -> dict:
    now = datetime.now(tz=ET).isoformat()
    model_name = str((artifact or {}).get("model_name", MODEL_DEFAULTS[model_key]["model_name"]))
    champion_path = str(MODEL_DEFAULTS[model_key]["champion"]).replace("\\", "/")
    current_version = str((artifact or {}).get("version", "missing"))

    if artifact is None:
        return {
            "as_of": now,
            "model_key": model_key,
            "model_name": model_name,
            "champion_path": champion_path,
            "current_version": current_version,
            "status": "ALERT",
            "drift_level": "RED",
            "recommendation": "RETRAIN_NOW",
            "action": "RETRAIN_NOW",
            "reason": f"Missing champion artifact for {model_key}; bootstrap retraining required.",
            "auto_retrain": True,
            "data_drift": 1.0,
            "concept_drift": 1.0,
            "calibration_drift": 1.0,
            "performance_decay": 1.0,
            "thresholds": cfg,
            "summary": {"missing_artifact": True},
        }

    reference_summary = artifact.get("dataset_summary") or current_summary
    eval_rows = _split_eval_rows(current_rows)
    shared = _feature_drift(reference_summary, current_summary, cfg)
    schema = _schema_drift(reference_summary, current_summary, cfg)
    target = _value_target_drift(reference_summary, current_summary, cfg) if model_key == "value" else _timing_target_drift(reference_summary, current_summary, cfg)
    performance = _value_performance(artifact, eval_rows, cfg) if model_key == "value" else _timing_performance(artifact, eval_rows, cfg)

    overall_state = _worst_state([shared["state"], schema["state"], target["state"], performance["state"]])
    recommendation = _recommendation_from_state(overall_state)
    auto_retrain = recommendation == "RETRAIN_NOW"

    reasons = []
    if shared["state"] != "GREEN":
        reasons.append(f"shared_data={shared['state']}")
    if schema["state"] != "GREEN":
        reasons.append(f"schema={schema['state']}")
    if target["state"] != "GREEN":
        reasons.append(f"target={target['state']}")
    if performance["state"] != "GREEN":
        reasons.append(f"performance={performance['state']}")
    if not reasons:
        reasons.append("All review components remain within thresholds.")

    return {
        "as_of": now,
        "model_key": model_key,
        "model_name": model_name,
        "champion_path": champion_path,
        "current_version": current_version,
        "status": _status_from_state(overall_state),
        "drift_level": overall_state,
        "recommendation": recommendation,
        "action": recommendation,
        "reason": "; ".join(reasons),
        "auto_retrain": auto_retrain,
        "data_drift": shared["score"],
        "concept_drift": target["score"],
        "calibration_drift": schema["score"],
        "performance_decay": performance["score"],
        "thresholds": cfg,
        "summary": {
            "shared_data": shared,
            "schema": schema,
            "target": target,
            "performance": performance,
        },
    }


def run_training_review(data_dir: Path, output_path: Path, summary_path: Path, config_path: Path | None = None) -> dict:
    logger.info("[retraining] start review data_dir=%s output_path=%s summary_path=%s", data_dir, output_path, summary_path)
    config = load_simple_yaml(config_path or Path("config/retraining.yaml"))
    dataset_path = data_dir / "training" / "modelable_dataset.json"
    current_rows = _load_dataset_rows(dataset_path) if dataset_path.exists() else []
    current_summary = summarize_modelable_rows(current_rows)

    artifacts = {
        model_key: _read_artifact(data_dir / "training" / "models" / f"{model_key}_champion.json")
        for model_key in MODEL_KEYS
    }

    records = {
        model_key: _build_record(model_key, artifacts[model_key], current_rows, current_summary, config)
        for model_key in MODEL_KEYS
    }

    for model_key in MODEL_KEYS:
        append_jsonl(output_path, records[model_key])

    tasks_for_auto_retrain = [model_key for model_key, record in records.items() if record["auto_retrain"]]
    suite_state = _worst_state([record["drift_level"] for record in records.values()])
    summary = {
        "as_of": datetime.now(tz=ET).isoformat(),
        "suite_status": _status_from_state(suite_state),
        "suite_recommendation": _recommendation_from_state(suite_state),
        "suite_version": format_champion_version(artifacts.get("value"), artifacts.get("timing")),
        "tasks_for_auto_retrain": tasks_for_auto_retrain,
        "models": records,
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("[retraining] review summary tasks_for_auto_retrain=%s", tasks_for_auto_retrain)
    return summary
