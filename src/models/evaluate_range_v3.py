"""Leakage-audited comparison of robust range challengers and incumbents."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from models.classic_champion_gate import _artifact_compatible, _evaluate_artifact
from models.train_classic_horizons import (
    FEATURES_BY_FAMILY,
    HORIZON_TARGETS,
    _load_rows,
    _prepare_rows,
)


ERROR_KEYS = ("mae_floor_pct", "mae_ceiling_pct", "mae_spread_pct")
MAX_INTERVAL_COVERAGE_REGRESSION = 0.05


def _load_artifact(directory: Path, horizon: str) -> dict[str, Any]:
    path = directory / f"{horizon}_champion.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Artifact must be a mapping: {path}")
    compatible, reason = _artifact_compatible(payload, horizon)
    if not compatible:
        raise ValueError(f"Incompatible artifact {path}: {reason}")
    return payload


def _prepared_split(rows: list[dict], horizon: str, split: str) -> list[Any]:
    floor_col, ceiling_col = HORIZON_TARGETS[horizon]
    selected = [
        row
        for row in rows
        if row.get("split") == split
        and row.get(f"split_eligible_{horizon}") is True
    ]
    feature_names = tuple(
        sorted({name for values in FEATURES_BY_FAMILY.values() for name in values})
    )
    prepared = _prepare_rows(selected, floor_col, ceiling_col, feature_names)
    if not prepared:
        raise ValueError(f"No eligible {split} rows for horizon={horizon}")
    return prepared


def _comparison(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    horizon: str,
    evaluation: list[Any],
) -> dict[str, Any]:
    baseline_metrics = _evaluate_artifact(baseline, horizon, evaluation)
    candidate_metrics = _evaluate_artifact(candidate, horizon, evaluation)
    improvement = {
        key: (
            (float(baseline_metrics[key]) - float(candidate_metrics[key]))
            / max(float(baseline_metrics[key]), 1e-12)
        )
        for key in ERROR_KEYS
    }
    dominates = all(value > 0.0 for value in improvement.values())
    coverage_delta = (
        float(candidate_metrics["test_interval_coverage"])
        - float(baseline_metrics["test_interval_coverage"])
    )
    return {
        "rows": len(evaluation),
        "baseline_metrics": baseline_metrics,
        "candidate_metrics": candidate_metrics,
        "relative_improvement": improvement,
        "interval_coverage_delta": coverage_delta,
        "coverage_guard_pass": coverage_delta >= -MAX_INTERVAL_COVERAGE_REGRESSION,
        "strict_error_dominance": dominates,
        "production_guard_pass": (
            dominates and coverage_delta >= -MAX_INTERVAL_COVERAGE_REGRESSION
        ),
    }


def build_report(
    dataset_path: Path,
    baseline_dir: Path,
    candidate_dir: Path,
) -> dict[str, Any]:
    payload = json.loads(dataset_path.read_text(encoding="utf-8"))
    rows = _load_rows(dataset_path)
    horizons: dict[str, Any] = {}
    for horizon in HORIZON_TARGETS:
        baseline = _load_artifact(baseline_dir, horizon)
        candidate = _load_artifact(candidate_dir, horizon)
        horizons[horizon] = {
            "baseline_model": baseline.get("model_name"),
            "baseline_version": baseline.get("version"),
            "candidate_model": candidate.get("model_name"),
            "candidate_version": candidate.get("version"),
            "validation": _comparison(
                baseline,
                candidate,
                horizon,
                _prepared_split(rows, horizon, "validation"),
            ),
            "blind_test": _comparison(
                baseline,
                candidate,
                horizon,
                _prepared_split(rows, horizon, "test"),
            ),
        }

    validation_pass = all(
        value["validation"]["production_guard_pass"] for value in horizons.values()
    )
    blind_test_pass = all(
        value["blind_test"]["production_guard_pass"] for value in horizons.values()
    )
    split_policy = payload.get("split_policy") if isinstance(payload, dict) else None
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": str(dataset_path),
        "split_policy": split_policy,
        "selection_split": "validation",
        "test_used_for_selection": False,
        "test_role": "one-shot post-selection audit",
        "max_interval_coverage_regression": MAX_INTERVAL_COVERAGE_REGRESSION,
        "horizons": horizons,
        "validation_production_guard_pass": validation_pass,
        "blind_test_production_guard_pass": blind_test_pass,
        "promotion_ready": validation_pass and blind_test_pass,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--baseline-dir", required=True, type=Path)
    parser.add_argument("--candidate-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--require-dominance", action="store_true")
    args = parser.parse_args()

    report = build_report(args.dataset, args.baseline_dir, args.candidate_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.require_dominance and not report["promotion_ready"]:
        raise SystemExit("robust_range_v3 failed strict validation/test dominance")


if __name__ == "__main__":
    main()
