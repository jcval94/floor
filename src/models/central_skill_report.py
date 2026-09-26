from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


HORIZONS = ("d1", "w1")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _number(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _horizon_report(registry: Path, horizon: str) -> dict[str, Any]:
    artifact = _read_json(registry / f"{horizon}_champion.json")
    competition = _read_json(registry / f"{horizon}_competition.json")
    metrics = _mapping(artifact.get("metrics"))
    params = _mapping(artifact.get("params"))
    dummy = _mapping(params.get("dummy_benchmark"))
    artifact_benchmark = _mapping(params.get("central_benchmark"))
    competition_benchmark = _mapping(competition.get("central_benchmark"))

    active_skill = _mapping(competition_benchmark.get("active_skill_vs_atr"))
    if not active_skill:
        active_skill = _mapping(artifact_benchmark.get("skill_vs_atr"))
    spread_skill = _number(
        active_skill.get("spread", metrics.get("central_skill_vs_atr"))
    )
    floor_skill = _number(
        active_skill.get("floor", metrics.get("central_skill_floor_vs_atr"))
    )
    ceiling_skill = _number(
        active_skill.get("ceiling", metrics.get("central_skill_ceiling_vs_atr"))
    )

    atr_metrics = _mapping(competition_benchmark.get("atr_only_metrics"))
    atr_spread = _number(
        atr_metrics.get(
            "mae_spread_pct",
            dummy.get("atr_only_mae_spread_pct"),
        )
    )
    model_spread = _number(metrics.get("mae_spread_pct"))
    if atr_spread is not None and spread_skill is not None:
        model_spread = atr_spread * (1.0 - spread_skill)

    stability = _mapping(
        competition_benchmark.get(
            "active_temporal_stability",
            artifact_benchmark.get("temporal_stability"),
        )
    )
    verdict = (
        "MODEL_SUPERIOR"
        if spread_skill is not None and spread_skill > 0.0
        else "ATR_ONLY_SUPERIOR"
        if spread_skill is not None
        else "UNKNOWN"
    )
    message = (
        "Model shows positive central skill versus ATR-only."
        if verdict == "MODEL_SUPERIOR"
        else "ATR-only sigue siendo superior."
        if verdict == "ATR_ONLY_SUPERIOR"
        else "Central skill evidence is unavailable."
    )

    return {
        "horizon": horizon,
        "model_name": artifact.get("model_name"),
        "model_version": artifact.get("version"),
        "benchmark": "atr_only",
        "model_mae_spread_pct": model_spread,
        "atr_only_mae_spread_pct": atr_spread,
        "skill_vs_atr": spread_skill,
        "floor_skill_vs_atr": floor_skill,
        "ceiling_skill_vs_atr": ceiling_skill,
        "temporal_stability": stability,
        "verdict": verdict,
        "message": message,
        "promotion_decision": competition.get("registry_decision"),
        "promotion_reason": competition.get("registry_reason"),
        "coverage_used_for_selection": False,
        "test_used_for_selection": False,
    }


def build_central_skill_report(registry: Path) -> dict[str, Any]:
    horizons = {
        horizon: _horizon_report(registry, horizon)
        for horizon in HORIZONS
    }
    complete = all(
        item.get("skill_vs_atr") is not None
        and item.get("atr_only_mae_spread_pct") is not None
        for item in horizons.values()
    )
    return {
        "status": "OK" if complete else "INCOMPLETE",
        "benchmark": "atr_only",
        "selection_contract": {
            "central_forecast_only": True,
            "risk_geometry_separate": True,
            "coverage_used_for_selection": False,
            "coverage_used_for_directional_confidence": False,
            "test_used_for_selection": False,
            "atr_comparison_required_for_promotion": True,
        },
        "horizons": horizons,
    }


def write_central_skill_report(
    registry: Path,
    output: Path,
    *,
    require_complete: bool = False,
) -> dict[str, Any]:
    report = build_central_skill_report(registry)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if require_complete and report["status"] != "OK":
        raise RuntimeError(
            "Central skill report is incomplete for D1/W1; "
            "ATR-only benchmark evidence is required."
        )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Report D1/W1 central skill versus permanent ATR-only benchmark"
    )
    parser.add_argument("--registry", default="data/training/models")
    parser.add_argument(
        "--output",
        default="data/training/metrics/central_skill_report_latest.json",
    )
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    report = write_central_skill_report(
        Path(args.registry),
        Path(args.output),
        require_complete=args.require_complete,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
