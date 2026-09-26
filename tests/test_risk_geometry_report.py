from __future__ import annotations

import json
from pathlib import Path

import pytest

from contracts.model_contract import attach_model_contract
from models.risk_geometry_report import (
    build_risk_geometry_report,
    write_risk_geometry_report,
)


def _artifact(task: str, *, with_risk: bool) -> dict:
    artifact = {
        "model_name": f"robust_range_v3_{task}",
        "version": "v2",
        "metrics": {
            "mae_floor_pct": 0.01,
            "mae_ceiling_pct": 0.02,
            "mae_spread_pct": 0.015,
            "central_skill_vs_atr": 0.04,
            "central_skill_floor_vs_atr": -0.002,
            "central_skill_ceiling_vs_atr": 0.006,
        },
        "params": {
            "schema_version": 2,
            "floor": {},
            "ceiling": {},
            "timing": {},
            "confidence_calibration": {},
            "central_benchmark": {
                "benchmark": "atr_only",
                "skill_vs_atr": {
                    "spread": 0.04,
                    "floor": -0.002,
                    "ceiling": 0.006,
                },
                "temporal_stability": {
                    "periods": 7,
                    "periods_won_vs_atr": 7,
                },
            },
        },
    }
    if with_risk:
        artifact["params"]["risk_geometry"] = {
            "schema_version": 1,
            "method": "validation_residual_quantile",
            "target_marginal_coverage": 0.80,
            "floor_delta_addon": 0.012,
            "ceiling_delta_addon": 0.018,
            "calibration_rows": 100,
        }
        artifact["metrics"].update(
            {
                "risk_floor_coverage": 0.79,
                "risk_ceiling_coverage": 0.81,
                "risk_interval_coverage": 0.64,
            }
        )
        artifact = attach_model_contract(artifact, task)
    return artifact


def _write_registry(root: Path, *, complete: bool) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for task in ("d1", "w1", "q1"):
        payload = _artifact(task, with_risk=(complete or task != "q1"))
        (root / f"{task}_champion.json").write_text(
            json.dumps(payload),
            encoding="utf-8",
        )


def test_risk_geometry_report_summarizes_declared_contracts(tmp_path: Path) -> None:
    registry = tmp_path / "models"
    _write_registry(registry, complete=True)

    report = build_risk_geometry_report(registry)

    assert report["status"] == "OK"
    assert report["classic_contract_complete"] is True
    assert report["tasks"]["d1"]["contract_status"] == "declared_valid"
    assert report["tasks"]["d1"]["floor_stop_widening_pct_points"] == pytest.approx(1.2)
    assert report["tasks"]["d1"]["ceiling_stop_widening_pct_points"] == pytest.approx(1.8)
    assert report["tasks"]["d1"]["central_skill_vs_atr"] == pytest.approx(0.04)
    assert report["tasks"]["d1"]["central_benchmark"]["benchmark"] == "atr_only"


def test_risk_geometry_report_fails_closed_when_required(tmp_path: Path) -> None:
    registry = tmp_path / "models"
    output = tmp_path / "report.json"
    _write_registry(registry, complete=False)

    with pytest.raises(RuntimeError, match="q1"):
        write_risk_geometry_report(
            registry,
            output,
            require_complete=True,
        )

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "INCOMPLETE"
    assert payload["tasks"]["q1"]["risk_geometry_available"] is False
