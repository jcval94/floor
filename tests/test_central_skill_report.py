from __future__ import annotations

import json
from pathlib import Path

import pytest

from models.central_skill_report import (
    build_central_skill_report,
    write_central_skill_report,
)


def _write_horizon(registry: Path, horizon: str, skill: float) -> None:
    artifact = {
        "model_name": f"central_skill_ensemble_v1_{horizon}",
        "version": "v1",
        "metrics": {
            "mae_spread_pct": 0.011,
            "central_skill_vs_atr": skill,
            "central_skill_floor_vs_atr": -0.002,
            "central_skill_ceiling_vs_atr": 0.006,
        },
        "params": {
            "dummy_benchmark": {
                "atr_only_mae_spread_pct": 0.012,
            },
            "central_benchmark": {
                "skill_vs_atr": {
                    "spread": skill,
                    "floor": -0.002,
                    "ceiling": 0.006,
                },
                "temporal_stability": {
                    "periods": 7,
                    "periods_won_vs_atr": 7,
                    "period_win_rate_vs_atr": 1.0,
                    "recent_period_skill_vs_atr": 0.03,
                },
            },
        },
    }
    competition = {
        "registry_decision": "promote",
        "registry_reason": "passed permanent ATR gate",
        "central_benchmark": {
            "benchmark": "atr_only",
            "atr_only_metrics": {
                "mae_floor_pct": 0.01,
                "mae_ceiling_pct": 0.012,
                "mae_spread_pct": 0.012,
            },
            "active_skill_vs_atr": {
                "spread": skill,
                "floor": -0.002,
                "ceiling": 0.006,
            },
            "active_temporal_stability": {
                "periods": 7,
                "periods_won_vs_atr": 7,
                "period_win_rate_vs_atr": 1.0,
                "recent_period_skill_vs_atr": 0.03,
            },
            "coverage_used_for_selection": False,
            "test_used_for_selection": False,
        },
    }
    (registry / f"{horizon}_champion.json").write_text(
        json.dumps(artifact),
        encoding="utf-8",
    )
    (registry / f"{horizon}_competition.json").write_text(
        json.dumps(competition),
        encoding="utf-8",
    )


def test_central_skill_report_keeps_atr_as_explicit_benchmark(tmp_path: Path) -> None:
    registry = tmp_path / "models"
    registry.mkdir()
    _write_horizon(registry, "d1", 0.045)
    _write_horizon(registry, "w1", 0.038)

    report = build_central_skill_report(registry)

    assert report["status"] == "OK"
    assert report["benchmark"] == "atr_only"
    assert report["selection_contract"]["risk_geometry_separate"] is True
    assert report["selection_contract"]["coverage_used_for_selection"] is False
    assert (
        report["selection_contract"]["coverage_used_for_directional_confidence"]
        is False
    )
    assert report["selection_contract"]["test_used_for_selection"] is False
    assert report["horizons"]["d1"]["skill_vs_atr"] == pytest.approx(0.045)
    assert report["horizons"]["w1"]["skill_vs_atr"] == pytest.approx(0.038)
    assert report["horizons"]["d1"]["verdict"] == "MODEL_SUPERIOR"


def test_central_skill_report_says_atr_is_superior_when_skill_is_negative(
    tmp_path: Path,
) -> None:
    registry = tmp_path / "models"
    registry.mkdir()
    _write_horizon(registry, "d1", -0.01)
    _write_horizon(registry, "w1", 0.02)

    report = build_central_skill_report(registry)
    d1 = report["horizons"]["d1"]
    assert d1["verdict"] == "ATR_ONLY_SUPERIOR"
    assert d1["message"] == "ATR-only sigue siendo superior."


def test_central_skill_report_require_complete_fails_closed(tmp_path: Path) -> None:
    registry = tmp_path / "models"
    registry.mkdir()
    output = tmp_path / "report.json"

    with pytest.raises(RuntimeError, match="incomplete"):
        write_central_skill_report(
            registry,
            output,
            require_complete=True,
        )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "INCOMPLETE"
