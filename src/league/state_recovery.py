from __future__ import annotations

import argparse
import json
from pathlib import Path

from league.engine import recover_legacy_compact_base


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Recover exactly one legacy compact Strategy League EOD checkpoint "
            "into the authoritative hash-chained runtime state."
        )
    )
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--league-config", default="config/strategy_league.json")
    parser.add_argument(
        "--compact-base",
        default="data/metrics/strategy_league/live_base.json",
    )
    args = parser.parse_args()

    league_cfg = json.loads(Path(args.league_config).read_text(encoding="utf-8"))
    league_id = str(league_cfg["league_id"])
    state_dir = (
        Path(args.data_dir)
        / "metrics"
        / "strategy_league"
        / "runs"
        / league_id
    )
    result = recover_legacy_compact_base(
        state_dir,
        Path(args.compact_base),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
