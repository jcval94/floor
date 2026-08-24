from __future__ import annotations

import argparse
import json
from pathlib import Path


def publish_league_payload(data_dir: Path, output_path: Path) -> dict:
    source = data_dir / "metrics" / "strategy_league" / "leaderboard.json"
    if source.exists():
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}
    else:
        payload = {}
    if not isinstance(payload, dict) or not payload:
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
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish safe Strategy League data to GitHub Pages")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--output", default="site/data/strategy_league.json")
    args = parser.parse_args()
    payload = publish_league_payload(Path(args.data_dir), Path(args.output))
    print(json.dumps({"status": payload.get("status"), "sessions": payload.get("sessions")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
