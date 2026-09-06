from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from forecasting.parity_models import ParityChampionModelSet

CLASSIC_TASKS = ("d1", "w1", "q1")
ALL_TASKS = (*CLASSIC_TASKS, "value", "timing")


def _load(path: Path) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size <= 0:
        raise RuntimeError(f"missing or empty champion artifact: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid champion JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"champion payload must be an object: {path}")
    return {str(key): value for key, value in payload.items()}


def _schema2_params(payload: dict[str, Any], *, task: str) -> dict[str, Any]:
    params = payload.get("params")
    if not isinstance(params, dict):
        raise RuntimeError(f"champion task={task} missing params mapping")
    if int(params.get("schema_version") or 0) != 2:
        raise RuntimeError(
            f"champion task={task} is not serving-compatible schema_version=2"
        )
    return params


def validate_champion_payload(payload: dict[str, Any], *, task: str) -> None:
    params = _schema2_params(payload, task=task)
    if not str(payload.get("model_name") or ""):
        raise RuntimeError(f"champion task={task} missing model_name")
    if not str(payload.get("version") or ""):
        raise RuntimeError(f"champion task={task} missing version")

    if task in CLASSIC_TASKS:
        for side in ("floor", "ceiling"):
            if not isinstance(params.get(side), dict):
                raise RuntimeError(f"champion task={task} missing trained {side} params")
        calibration = params.get("confidence_calibration")
        if not isinstance(calibration, dict):
            raise RuntimeError(f"champion task={task} missing confidence calibration")
        if calibration.get("method") != "validation_empirical_interval_breach":
            raise RuntimeError(
                f"champion task={task} has unsupported confidence calibration"
            )
        timing = params.get("timing")
        if not isinstance(timing, dict) or int(timing.get("schema_version") or 0) != 2:
            raise RuntimeError(f"champion task={task} missing schema-v2 timing contract")
        return

    if task == "value":
        if params.get("target_space") != "relative_floor_delta":
            raise RuntimeError("m3 value champion must use relative_floor_delta")
        if params.get("loss") != "pinball_quantile":
            raise RuntimeError("m3 value champion must use pinball_quantile")
        return

    if task == "timing":
        if params.get("model_type") != "multinomial_logistic":
            raise RuntimeError("m3 timing champion must use multinomial_logistic")
        if int(params.get("class_count") or 0) != 13:
            raise RuntimeError("m3 timing champion must expose 13 classes")
        return

    raise RuntimeError(f"unknown champion task: {task}")


def _smoke_row() -> dict[str, Any]:
    return {
        "timestamp": "2026-08-21T16:00:00-04:00",
        "symbol": "SMOKE",
        "open": 100.0,
        "high": 102.0,
        "low": 98.0,
        "close": 100.0,
        "volume": 1_000_000.0,
        "atr_14": 2.0,
        "trend_context_m3": 0.04,
        "drawdown_13w": 0.08,
        "dist_to_low_3m": 0.10,
        "ai_horizon_alignment": 0.0,
        "rel_strength_20": 0.01,
        "momentum_20": 0.02,
        "ai_recency_long": 0.0,
    }


def validate_registry(registry_dir: Path, *, run_smoke: bool = True) -> dict[str, Any]:
    payloads: dict[str, dict[str, Any]] = {}
    for task in ALL_TASKS:
        payload = _load(registry_dir / f"{task}_champion.json")
        validate_champion_payload(payload, task=task)
        payloads[task] = payload

    summary: dict[str, Any] = {
        "registry": str(registry_dir),
        "tasks": {
            task: {
                "model_name": payload.get("model_name"),
                "version": payload.get("version"),
                "schema_version": payload.get("params", {}).get("schema_version"),
            }
            for task, payload in payloads.items()
        },
        "serving_smoke": False,
    }

    if run_smoke:
        model = ParityChampionModelSet(model_registry_dir=registry_dir)
        if not model.is_available:
            raise RuntimeError(f"serving model set unavailable diagnostics={model.load_diagnostics}")
        row = _smoke_row()
        d1 = model.predict_d1(row)
        w1 = model.predict_w1(row)
        q1 = model.predict_q1(row)
        m3 = model.predict_m3(row)
        for horizon, forecast in (("d1", d1), ("w1", w1), ("q1", q1)):
            if forecast.floor > forecast.ceiling:
                raise RuntimeError(f"serving smoke invalid interval horizon={horizon}")
        if m3 is None:
            raise RuntimeError("serving smoke unexpectedly abstained from m3")
        if not 1 <= int(m3.floor_week_m3) <= 13:
            raise RuntimeError("serving smoke m3 timing outside 1..13")
        summary["serving_smoke"] = True
        summary["model_version"] = model.version
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate that all champion artifacts satisfy the live serving contract"
    )
    parser.add_argument("--registry", default="data/training/models")
    parser.add_argument("--no-smoke", action="store_true")
    args = parser.parse_args()
    summary = validate_registry(Path(args.registry), run_smoke=not args.no_smoke)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
