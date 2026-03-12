from __future__ import annotations

import argparse
import json
from pathlib import Path

from monitoring.drift_detection import (
    evaluate_coverage_calibration_drift,
    evaluate_feature_data_drift,
    evaluate_m3_data_quality,
    evaluate_m3_value_and_timing_drift,
    evaluate_schema_and_coverage,
    evaluate_target_temporal_drift,
    evaluate_target_value_drift,
)
from monitoring.retraining_policy import (
    build_retraining_decision,
    evaluate_m3_performance_and_stability,
    evaluate_paper_trading_deterioration,
    evaluate_performance_deterioration,
    evaluate_timing_deterioration,
)
from monitoring.retraining_report import append_history, build_retraining_report, save_report


def _parse_scalar(value: str):
    raw = value.strip()
    if raw.lower() in {"true", "false"}:
        return raw.lower() == "true"
    try:
        if "." in raw:
            return float(raw)
        return int(raw)
    except ValueError:
        return raw.strip('"').strip("'")


def load_simple_yaml(path: Path) -> dict:
    root: dict = {}
    stack: list[tuple[int, dict]] = [(-1, root)]

    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        key, _, value = line.strip().partition(":")
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if value.strip() == "":
            parent[key] = {}
            stack.append((indent, parent[key]))
        else:
            parent[key] = _parse_scalar(value)
    return root


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _m3_metrics(metrics: dict, prefix: str) -> dict:
    return {
        "pinball_loss_m3": float(metrics.get(f"{prefix}pinball_loss_m3", metrics.get("pinball_loss_m3", 0.0))),
        "top1_accuracy_m3": float(metrics.get(f"{prefix}top1_accuracy_m3", metrics.get("top1_accuracy_m3", 0.0))),
        "top3_accuracy_m3": float(metrics.get(f"{prefix}top3_accuracy_m3", metrics.get("top3_accuracy_m3", 0.0))),
        "week_distance_m3": float(metrics.get(f"{prefix}week_distance_m3", metrics.get("week_distance_m3", 0.0))),
        "champion_flip_rate_m3": float(metrics.get(f"{prefix}champion_flip_rate_m3", metrics.get("champion_flip_rate_m3", 0.0))),
    }


def run_assessment(reference: dict, current: dict, timing_ref: dict, timing_cur: dict, paper_ref: dict, paper_cur: dict, cfg: dict) -> dict:
    important_features = [x.strip() for x in str(cfg["important_features"]["columns"]).split(",") if x.strip()]

    feature_drift = evaluate_feature_data_drift(
        reference_rows=reference["rows"],
        current_rows=current["rows"],
        important_features=important_features,
        psi_warn=float(cfg["thresholds"]["feature_psi_warn"]),
        psi_fail=float(cfg["thresholds"]["feature_psi_fail"]),
    )
    target_value_drift = evaluate_target_value_drift(
        reference_rows=reference["rows"],
        current_rows=current["rows"],
        target_cols=["floor_d1", "ceiling_d1", "floor_w1", "ceiling_w1", "floor_q1", "ceiling_q1"],
        js_warn=float(cfg["thresholds"]["target_js_warn"]),
        js_fail=float(cfg["thresholds"]["target_js_fail"]),
    )
    target_temporal_drift = evaluate_target_temporal_drift(
        reference_rows=reference["rows"],
        current_rows=current["rows"],
        temporal_cols=["floor_time_bucket_d1", "ceiling_time_bucket_d1", "floor_day_w1", "ceiling_day_w1", "floor_day_q1", "ceiling_day_q1"],
        js_warn=float(cfg["thresholds"]["temporal_js_warn"]),
        js_fail=float(cfg["thresholds"]["temporal_js_fail"]),
    )
    coverage_calibration = evaluate_coverage_calibration_drift(
        reference_metrics=reference["coverage"],
        current_metrics=current["coverage"],
        warn=float(cfg["thresholds"]["coverage_calibration_warn"]),
        fail=float(cfg["thresholds"]["coverage_calibration_fail"]),
    )
    schema = evaluate_schema_and_coverage(
        reference_schema=reference["schema"],
        current_schema=current["schema"],
        min_coverage=float(cfg["thresholds"]["min_column_coverage"]),
    )
    performance = evaluate_performance_deterioration(
        reference_perf=reference["performance"],
        current_perf=current["performance"],
        thresholds=cfg["performance_thresholds"],
    )
    timing = evaluate_timing_deterioration(
        reference_timing=timing_ref,
        current_timing=timing_cur,
        thresholds=cfg["timing_thresholds"],
    )
    paper = evaluate_paper_trading_deterioration(
        reference_paper=paper_ref,
        current_paper=paper_cur,
        thresholds=cfg["paper_thresholds"],
    )

    m3_drift = evaluate_m3_value_and_timing_drift(
        reference_rows=reference["rows"],
        current_rows=current["rows"],
        thresholds=cfg["m3_drift_thresholds"],
    )
    m3_quality = evaluate_m3_data_quality(
        reference_rows=reference["rows"],
        current_rows=current["rows"],
        required_cols=[
            "floor_m3",
            "realized_floor_m3",
            "floor_week_m3",
            "floor_week_m3_confidence",
            "expected_return_m3",
            "expected_range_m3",
        ],
        min_coverage=float(cfg["m3_drift_thresholds"]["min_m3_column_coverage"]),
    )
    m3_performance = evaluate_m3_performance_and_stability(
        reference_m3=_m3_metrics(reference.get("performance", {}), prefix=""),
        current_m3=_m3_metrics(current.get("performance", {}), prefix=""),
        thresholds=cfg["m3_performance_thresholds"],
    )

    decision = build_retraining_decision(
        {
            "feature_data_drift": feature_drift,
            "target_value_drift": target_value_drift,
            "target_temporal_drift": target_temporal_drift,
            "coverage_calibration_drift": coverage_calibration,
            "schema_and_coverage": schema,
            "performance_deterioration": performance,
            "timing_deterioration": timing,
            "paper_trading_deterioration": paper,
            "m3_value_timing_drift": m3_drift,
            "m3_data_quality": m3_quality,
            "m3_performance_stability": m3_performance,
        },
        retraining_cfg=cfg.get("review", {}),
    )

    return decision


def main() -> None:
    parser = argparse.ArgumentParser(description="Run quincenal retraining assessment")
    parser.add_argument("--reference", required=True, help="Reference window JSON")
    parser.add_argument("--current", required=True, help="Current window JSON")
    parser.add_argument("--timing-reference", required=True, help="Reference timing metrics JSON")
    parser.add_argument("--timing-current", required=True, help="Current timing metrics JSON")
    parser.add_argument("--paper-reference", required=True, help="Reference paper metrics JSON")
    parser.add_argument("--paper-current", required=True, help="Current paper metrics JSON")
    parser.add_argument("--config", default="config/retraining.yaml", help="Config yaml")
    parser.add_argument("--output", required=True, help="Output report JSON")
    parser.add_argument("--history", required=True, help="History JSONL path")
    args = parser.parse_args()

    cfg = load_simple_yaml(Path(args.config))
    ref = _load_json(Path(args.reference))
    cur = _load_json(Path(args.current))
    timing_ref = _load_json(Path(args.timing_reference))
    timing_cur = _load_json(Path(args.timing_current))
    paper_ref = _load_json(Path(args.paper_reference))
    paper_cur = _load_json(Path(args.paper_current))

    decision = run_assessment(ref, cur, timing_ref, timing_cur, paper_ref, paper_cur, cfg)
    report = build_retraining_report(
        decision=decision,
        inputs_snapshot={
            "reference_rows": len(ref.get("rows", [])),
            "current_rows": len(cur.get("rows", [])),
        },
        config_snapshot=cfg,
    )
    save_report(Path(args.output), report)
    append_history(Path(args.history), report)


if __name__ == "__main__":
    main()
