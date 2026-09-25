from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from contracts.model_contract import validate_model_artifact_contract


CLASSIC_TASKS = ("d1", "w1", "q1")


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Expected JSON object: {path}")
    return payload


def build_risk_geometry_report(registry_dir: Path) -> dict[str, Any]:
    tasks: dict[str, Any] = {}
    complete = True

    for task in CLASSIC_TASKS:
        path = registry_dir / f"{task}_champion.json"
        artifact = _load(path)
        contract = validate_model_artifact_contract(
            task,
            artifact,
            allow_legacy=True,
        )
        params = artifact.get("params")
        params = params if isinstance(params, dict) else {}
        metrics = artifact.get("metrics")
        metrics = metrics if isinstance(metrics, dict) else {}
        risk = params.get("risk_geometry")
        risk = risk if isinstance(risk, dict) else {}
        migration = artifact.get("contract_migration")
        migration = migration if isinstance(migration, dict) else {}

        risk_available = bool(risk) and contract.get("status") == "declared_valid"
        complete = complete and risk_available

        floor_addon = float(risk.get("floor_delta_addon") or 0.0)
        ceiling_addon = float(risk.get("ceiling_delta_addon") or 0.0)
        tasks[task] = {
            "model_name": artifact.get("model_name"),
            "version": artifact.get("version"),
            "contract_status": contract.get("status"),
            "contract_id": contract.get("contract_id"),
            "risk_geometry_available": risk_available,
            "risk_geometry_method": risk.get("method"),
            "target_joint_coverage": risk.get("target_joint_coverage"),
            "target_marginal_coverage": risk.get("target_marginal_coverage"),
            "calibration_rows": risk.get("calibration_rows"),
            "floor_stop_widening_pct_points": 100.0 * floor_addon,
            "ceiling_stop_widening_pct_points": 100.0 * ceiling_addon,
            "risk_floor_coverage": metrics.get("risk_floor_coverage"),
            "risk_ceiling_coverage": metrics.get("risk_ceiling_coverage"),
            "risk_interval_coverage": metrics.get("risk_interval_coverage"),
            "risk_joint_coverage_error": metrics.get("risk_joint_coverage_error"),
            "risk_mean_width_pct": metrics.get("risk_mean_width_pct"),
            "central_skill_vs_best_dummy": metrics.get(
                "central_skill_vs_best_dummy"
            ),
            "dummy_benchmark": params.get("dummy_benchmark"),
            "central_mae_floor_pct": metrics.get("mae_floor_pct"),
            "central_mae_ceiling_pct": metrics.get("mae_ceiling_pct"),
            "central_mae_spread_pct": metrics.get("mae_spread_pct"),
            "central_model_preserved": migration.get("central_model_preserved"),
            "contract_migration_from_version": migration.get("from_version"),
        }

    return {
        "schema_version": 2,
        "status": "OK" if complete else "INCOMPLETE",
        "classic_contract_complete": complete,
        "tasks": tasks,
    }


def write_risk_geometry_report(
    registry_dir: Path,
    output_path: Path,
    *,
    require_complete: bool = False,
) -> dict[str, Any]:
    payload = build_risk_geometry_report(registry_dir)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if require_complete and not payload["classic_contract_complete"]:
        missing = [
            task
            for task, row in payload["tasks"].items()
            if not row["risk_geometry_available"]
        ]
        raise RuntimeError(
            "Classic risk geometry contract incomplete: " + ",".join(missing)
        )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Summarize D1/W1/Q1 risk-geometry contract health"
    )
    parser.add_argument("--registry", default="data/training/models")
    parser.add_argument(
        "--output",
        default="data/training/metrics/risk_geometry_report_latest.json",
    )
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()

    payload = write_risk_geometry_report(
        Path(args.registry),
        Path(args.output),
        require_complete=args.require_complete,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
