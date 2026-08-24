from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _load_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_object(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def publish_league_payload(data_dir: Path, output_path: Path) -> dict[str, Any]:
    source = data_dir / "metrics" / "strategy_league" / "leaderboard.json"
    payload = _load_object(source)
    if not payload:
        payload = {
            "schema_version": 1,
            "league_id": "strategy_league_v1",
            "mode": "shadow_paper",
            "status": "WAITING_FOR_WEEKLY_MODEL",
            "detail": "The prospective league has not started yet.",
            "start_session": None,
            "last_session": None,
            "sessions": 0,
            "initial_nav_usd": 100000.0,
            "automatic_promotion": False,
            "live_execution_enabled": False,
            "rows": [],
        }
    payload["automatic_promotion"] = False
    payload["live_execution_enabled"] = False
    _write_object(output_path, payload)
    return payload


def publish_observation_payload(
    data_dir: Path,
    output_path: Path,
) -> dict[str, Any]:
    source = (
        data_dir
        / "metrics"
        / "strategy_league"
        / "experiment_observation.json"
    )
    payload = _load_object(source)
    if not payload:
        payload = {
            "schema_version": 1,
            "status": "WAITING_FOR_GENESIS",
            "start_session": None,
            "last_session": None,
            "sessions": 0,
            "strategy_league": {
                "status": "WAITING",
                "rows": [],
                "automatic_promotion": False,
                "live_execution_enabled": False,
            },
            "models": {
                "scope": "predictions created on or after Strategy League genesis",
                "horizons": [],
                "weekly_opportunity_challenger": {
                    "status": "WAITING",
                    "version": None,
                    "validation_metrics": {},
                },
            },
            "evidence": {
                "prediction_count_since_genesis": 0,
                "reconciled_count_since_genesis": 0,
            },
            "safety": {
                "operational_paper_gateway_used": False,
                "live_execution_enabled": False,
                "automatic_promotion": False,
            },
        }
    payload.setdefault("safety", {})
    payload["safety"]["operational_paper_gateway_used"] = False
    payload["safety"]["live_execution_enabled"] = False
    payload["safety"]["automatic_promotion"] = False
    _write_object(output_path, payload)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Publish safe Strategy League data to GitHub Pages"
    )
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--output", default="site/data/strategy_league.json")
    parser.add_argument(
        "--observation-output",
        default="site/data/experiment_observation.json",
    )
    args = parser.parse_args()
    payload = publish_league_payload(Path(args.data_dir), Path(args.output))
    observation = publish_observation_payload(
        Path(args.data_dir),
        Path(args.observation_output),
    )
    print(
        json.dumps(
            {
                "status": payload.get("status"),
                "sessions": payload.get("sessions"),
                "observation_status": observation.get("status"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
