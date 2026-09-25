from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from contracts.config_io import load_simple_yaml
from contracts.trading import load_strategy_runtime_config
from strategies.registry import STRATEGY_GENERATORS
from strategies.relative_strength_rotation import build_relative_strength_rotation
from strategies.research_registry import (
    RESEARCH_ONLY_STRATEGY_GENERATORS,
    RESEARCH_ONLY_STRATEGY_IDS,
)
from strategies.volatility_regime_switch import route_volatility_regime_decisions


def _load_rows(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [dict(row) for row in payload if isinstance(row, dict)]
    rows = payload.get("rows", payload.get("dataset_forecasts", []))
    return [dict(row) for row in rows if isinstance(row, dict)]


def _eligible(row: dict, strategy_cfg: dict) -> bool:
    universe = strategy_cfg.get("universe", {})
    close = float(row.get("close") or row.get("open") or 0.0)
    min_price = float(universe.get("min_price", 0.0) or 0.0)
    max_price = float(universe.get("max_price", float("inf")) or float("inf"))
    if close < min_price or close > max_price:
        return False
    excluded = {
        item.strip()
        for item in str(universe.get("excluded_sectors") or "").split(",")
        if item.strip()
    }
    return str(row.get("sector") or "") not in excluded


def _source_decisions(
    source_ids: list[str],
    rows: list[dict],
    global_cfg: dict,
    session: str,
) -> dict[str, list]:
    output: dict[str, list] = {}
    active_cfg = global_cfg.get("strategies", {})
    for source_id in source_ids:
        generator = STRATEGY_GENERATORS.get(source_id)
        strategy_cfg = active_cfg.get(source_id)
        if generator is None or not isinstance(strategy_cfg, dict):
            continue
        source_rows = [
            row for row in rows if _eligible(row, strategy_cfg)
        ]
        output[source_id] = generator(
            source_rows,
            global_cfg,
            strategy_cfg,
            session,
        )
    return output


def run_research_strategy(
    strategy_id: str,
    rows: list[dict],
    global_cfg: dict,
    research_cfg: dict,
    session: str,
    *,
    held_symbols: set[str] | None = None,
) -> list:
    strategy_cfg = dict(research_cfg.get("strategy", {}))
    rows_by_symbol = {
        str(row.get("symbol")): row
        for row in rows
        if row.get("symbol")
    }

    if strategy_id in RESEARCH_ONLY_STRATEGY_GENERATORS:
        generator = RESEARCH_ONLY_STRATEGY_GENERATORS[strategy_id]
        return generator(rows, global_cfg, strategy_cfg, session)

    if strategy_id == "volatility_regime_switch":
        low_source = str(
            strategy_cfg.get("low_source") or "mean_reversion_floor_w1"
        )
        high_source = str(
            strategy_cfg.get("high_source") or "breakout_protected_by_floor"
        )
        decisions = _source_decisions(
            list(dict.fromkeys([low_source, high_source])),
            rows,
            global_cfg,
            session,
        )
        return route_volatility_regime_decisions(
            decisions,
            rows_by_symbol,
            strategy_cfg,
        )

    if strategy_id == "relative_strength_rotation":
        source_ids = [
            item.strip()
            for item in str(strategy_cfg.get("source_priority") or "").split(",")
            if item.strip()
        ]
        decisions = _source_decisions(
            source_ids,
            rows,
            global_cfg,
            session,
        )
        return build_relative_strength_rotation(
            decisions,
            rows_by_symbol,
            strategy_cfg,
            held_symbols=held_symbols,
        )

    raise ValueError(f"Unsupported research strategy: {strategy_id}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run an isolated FLOOR research-only strategy"
    )
    parser.add_argument(
        "--strategy",
        required=True,
        choices=sorted(RESEARCH_ONLY_STRATEGY_IDS),
    )
    parser.add_argument("--rows", required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--held-symbols", default="")
    parser.add_argument(
        "--strategies-config",
        default="config/strategies.yaml",
    )
    parser.add_argument(
        "--research-config",
        default=None,
        help="Defaults to config/research/<strategy>.yaml",
    )
    args = parser.parse_args()

    global_cfg = load_strategy_runtime_config(Path(args.strategies_config))
    research_path = Path(
        args.research_config
        or f"config/research/{args.strategy}.yaml"
    )
    research_cfg = load_simple_yaml(research_path)
    rows = _load_rows(Path(args.rows))
    held_symbols = {
        item.strip().upper()
        for item in str(args.held_symbols or "").split(",")
        if item.strip()
    }

    decisions = run_research_strategy(
        args.strategy,
        rows,
        global_cfg,
        research_cfg,
        args.session,
        held_symbols=held_symbols,
    )
    payload = {
        "strategy_id": args.strategy,
        "evidence_role": "diagnostic_only",
        "counts_as_strategy_league_evidence": False,
        "session": args.session,
        "rows": len(rows),
        "action_counts": {
            action: sum(
                1 for item in decisions if str(item.side).upper() == action
            )
            for action in ("BUY", "SELL", "HOLD")
        },
        "decisions": [asdict(item) for item in decisions],
    }
    Path(args.output).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
