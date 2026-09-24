from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from contracts.model_contract import attach_model_contract
from models.classic_horizon_predictor import model_family, predict_family_delta
from models.temporal_cv import purged_chronological_calibration_split
from models.train_classic_horizons import (
    FEATURES_BY_FAMILY,
    HORIZON_TARGETS,
    _load_rows,
    _prepare_rows,
    _risk_geometry_metrics,
    _split,
    _quantile,
)


MIGRATION_SCHEMA_VERSION = 1
TARGET_MARGINAL_COVERAGE = 0.80
CONSERVATIVE_CALIBRATION_QUANTILE = 0.90
MAX_OOT_COVERAGE_SHORTFALL = 0.05


def _canonical_sha(payload: object) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _central_payload(artifact: dict[str, Any]) -> dict[str, Any]:
    params = artifact.get("params")
    if not isinstance(params, dict):
        params = {}
    return {
        "model_name": artifact.get("model_name"),
        "horizon": artifact.get("horizon"),
        "target": artifact.get("target"),
        "version": artifact.get("version"),
        "floor": deepcopy(params.get("floor")),
        "ceiling": deepcopy(params.get("ceiling")),
        "timing": deepcopy(params.get("timing")),
        "confidence_calibration": deepcopy(params.get("confidence_calibration")),
    }


def _fit_conservative_risk_geometry(
    rows: list[Any],
    floor_predictions: list[float],
    ceiling_predictions: list[float],
) -> dict[str, Any]:
    floor_residuals = [
        item.floor_delta - prediction
        for item, prediction in zip(rows, floor_predictions)
    ]
    ceiling_residuals = [
        item.ceiling_delta - prediction
        for item, prediction in zip(rows, ceiling_predictions)
    ]
    return {
        "schema_version": 1,
        "method": "validation_residual_quantile",
        "target_marginal_coverage": TARGET_MARGINAL_COVERAGE,
        "calibration_quantile": CONSERVATIVE_CALIBRATION_QUANTILE,
        "floor_delta_addon": max(
            0.0,
            _quantile(floor_residuals, CONSERVATIVE_CALIBRATION_QUANTILE),
        ),
        "ceiling_delta_addon": max(
            0.0,
            _quantile(ceiling_residuals, CONSERVATIVE_CALIBRATION_QUANTILE),
        ),
        "calibration_rows": len(rows),
        "semantics": {
            "central_geometry": "unchanged historical champion predictor",
            "risk_geometry": (
                "conservative one-sided residual-quantile boundary calibrated "
                "without modifying the central predictor"
            ),
            "strategy_stop_source": "risk_geometry",
            "strategy_target_source": "central_geometry",
        },
    }


def _artifact_predictions(
    artifact: dict[str, Any],
    rows: list[Any],
) -> tuple[list[float], list[float]]:
    family = model_family(str(artifact.get("model_name") or ""))
    params = artifact.get("params")
    if not family or not isinstance(params, dict):
        raise ValueError("classic artifact has unsupported model family or params")

    floor_params = params.get("floor")
    ceiling_params = params.get("ceiling")
    if not isinstance(floor_params, dict) or not isinstance(ceiling_params, dict):
        raise ValueError("classic artifact missing floor/ceiling params")

    floors = [
        predict_family_delta(
            family,
            floor_params,
            item.features,
            validate=True,
        )
        for item in rows
    ]
    ceilings = [
        predict_family_delta(
            family,
            ceiling_params,
            item.features,
            validate=True,
        )
        for item in rows
    ]
    return floors, ceilings


def migrate_one(
    *,
    rows: list[dict],
    artifact: dict[str, Any],
    horizon: str,
    migration_version: str,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    if horizon not in HORIZON_TARGETS:
        raise ValueError(f"unsupported classic horizon: {horizon}")

    floor_col, ceiling_col = HORIZON_TARGETS[horizon]
    _train_raw, validation_raw = _split(rows, horizon)
    calibration_raw, evaluation_raw = purged_chronological_calibration_split(
        validation_raw,
        target_end_field=f"target_end_date_{horizon}",
    )
    feature_names = tuple(
        sorted({name for values in FEATURES_BY_FAMILY.values() for name in values})
    )
    calibration = _prepare_rows(
        calibration_raw,
        floor_col,
        ceiling_col,
        feature_names,
    )
    evaluation = _prepare_rows(
        evaluation_raw,
        floor_col,
        ceiling_col,
        feature_names,
    )
    if not calibration or not evaluation:
        raise ValueError(
            f"classic contract migration lacks purged calibration/evaluation rows horizon={horizon}"
        )

    calibration_floor, calibration_ceiling = _artifact_predictions(
        artifact,
        calibration,
    )
    risk_geometry = _fit_conservative_risk_geometry(
        calibration,
        calibration_floor,
        calibration_ceiling,
    )

    evaluation_floor, evaluation_ceiling = _artifact_predictions(
        artifact,
        evaluation,
    )
    risk_metrics = _risk_geometry_metrics(
        evaluation,
        evaluation_floor,
        evaluation_ceiling,
        risk_geometry,
    )

    required_coverage = (
        TARGET_MARGINAL_COVERAGE - MAX_OOT_COVERAGE_SHORTFALL
    )
    coverage_ready = (
        float(risk_metrics["risk_floor_coverage"]) >= required_coverage
        and float(risk_metrics["risk_ceiling_coverage"]) >= required_coverage
    )

    central_before = _central_payload(artifact)
    central_hash = _canonical_sha(central_before)

    report: dict[str, Any] = {
        "schema_version": MIGRATION_SCHEMA_VERSION,
        "horizon": horizon,
        "source_model_name": artifact.get("model_name"),
        "source_model_version": artifact.get("version"),
        "migration_version": migration_version,
        "status": "READY" if coverage_ready else "BLOCKED_COVERAGE",
        "target_marginal_coverage": TARGET_MARGINAL_COVERAGE,
        "calibration_quantile": CONSERVATIVE_CALIBRATION_QUANTILE,
        "max_oot_coverage_shortfall": MAX_OOT_COVERAGE_SHORTFALL,
        "required_oot_marginal_coverage": required_coverage,
        "calibration_rows": len(calibration),
        "evaluation_rows": len(evaluation),
        "risk_geometry": risk_geometry,
        "risk_metrics": risk_metrics,
        "central_contract_sha256_before": central_hash,
        "central_predictor_changed": False,
    }
    if not coverage_ready:
        return None, report

    migrated = deepcopy(artifact)
    params = migrated.get("params")
    if not isinstance(params, dict):
        raise ValueError("classic artifact params must be an object")
    params["risk_geometry"] = risk_geometry
    migrated["params"] = params
    migrated = attach_model_contract(migrated, horizon)
    migrated["contract_migration"] = {
        "schema_version": MIGRATION_SCHEMA_VERSION,
        "migration_version": migration_version,
        "source_model_version": artifact.get("version"),
        "source_artifact_central_sha256": central_hash,
        "central_predictor_changed": False,
        "coverage_gate": {
            "required_oot_marginal_coverage": required_coverage,
            **risk_metrics,
        },
    }

    central_after = _central_payload(migrated)
    after_hash = _canonical_sha(central_after)
    report["central_contract_sha256_after"] = after_hash
    report["central_predictor_changed"] = after_hash != central_hash
    if after_hash != central_hash:
        raise RuntimeError(
            f"contract migration changed central predictor horizon={horizon}"
        )
    return migrated, report


def run_migration(
    *,
    dataset_path: Path,
    registry_dir: Path,
    output_dir: Path,
    migration_version: str,
    tasks: list[str] | tuple[str, ...] = ("d1", "w1", "q1"),
    require_ready: bool = False,
) -> dict[str, Any]:
    rows = _load_rows(dataset_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    reports: list[dict[str, Any]] = []

    for horizon in tasks:
        path = registry_dir / f"{horizon}_champion.json"
        artifact = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(artifact, dict):
            raise ValueError(f"classic champion must be an object: {path}")

        migrated, report = migrate_one(
            rows=rows,
            artifact=artifact,
            horizon=horizon,
            migration_version=migration_version,
        )
        reports.append(report)
        if migrated is not None:
            (output_dir / f"{horizon}_champion.json").write_text(
                json.dumps(migrated, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

    ready = all(report["status"] == "READY" for report in reports)
    summary = {
        "schema_version": MIGRATION_SCHEMA_VERSION,
        "migration_version": migration_version,
        "ready": ready,
        "requires_new_central_training": not ready,
        "reports": reports,
    }
    (output_dir / "migration_report.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if require_ready and not ready:
        blocked = [
            str(report["horizon"])
            for report in reports
            if report["status"] != "READY"
        ]
        raise RuntimeError(
            "classic contract migration blocked by OOT risk coverage: "
            + ",".join(blocked)
        )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Attach risk-geometry/model contracts to existing classic champions "
            "without modifying their central predictors"
        )
    )
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--registry-dir", default="data/training/models")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--tasks", default="d1,w1,q1")
    parser.add_argument("--require-ready", action="store_true")
    args = parser.parse_args()
    tasks = [
        part.strip()
        for part in str(args.tasks).split(",")
        if part.strip()
    ]
    summary = run_migration(
        dataset_path=Path(args.dataset),
        registry_dir=Path(args.registry_dir),
        output_dir=Path(args.output_dir),
        migration_version=args.version,
        tasks=tasks,
        require_ready=args.require_ready,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
