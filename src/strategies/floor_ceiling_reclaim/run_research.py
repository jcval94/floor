from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from contracts.config_io import load_simple_yaml
from contracts.trading import load_strategy_runtime_config
from strategies.floor_ceiling_reclaim import generate_floor_ceiling_reclaim_orders


def _load_rows(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [dict(row) for row in payload if isinstance(row, dict)]
    rows = payload.get("rows", payload.get("dataset_forecasts", []))
    return [dict(row) for row in rows if isinstance(row, dict)]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the isolated Floor/Ceiling reclaim research challenger"
    )
    parser.add_argument("--rows", required=True, help="Point-in-time intraday rows JSON")
    parser.add_argument("--session", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--research-config",
        default="config/research/floor_ceiling_reclaim.yaml",
    )
    parser.add_argument(
        "--strategies-config",
        default="config/strategies.yaml",
    )
    args = parser.parse_args()

    global_cfg = load_strategy_runtime_config(Path(args.strategies_config))
    research_cfg = load_simple_yaml(Path(args.research_config))
    strategy_cfg = dict(research_cfg.get("strategy", {}))
    rows = _load_rows(Path(args.rows))

    decisions = generate_floor_ceiling_reclaim_orders(
        rows,
        global_cfg,
        strategy_cfg,
        args.session,
    )
    payload = {
        "strategy_id": "floor_ceiling_reclaim",
        "evidence_role": "diagnostic_only",
        "counts_as_strategy_league_evidence": False,
        "decisions": [asdict(decision) for decision in decisions],
    }
    Path(args.output).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
