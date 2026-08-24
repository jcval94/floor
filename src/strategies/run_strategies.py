from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Callable

from strategies.activation import VALID_MODES, activation_snapshot
from strategies.base import StrategyDecision
from strategies.portfolio_allocator import allocate_orders
from strategies.strategy_ai_only import generate_ai_only_orders
from strategies.strategy_breakout_floor import generate_breakout_floor_orders
from strategies.strategy_consensus import generate_consensus_orders
from strategies.strategy_mean_reversion import generate_mean_reversion_orders
from strategies.strategy_model_only import generate_model_only_orders
from strategies.strategy_weekly_opportunity import generate_weekly_opportunity_orders


StrategyGenerator = Callable[[list[dict], dict, dict, str], list[StrategyDecision]]

STRATEGY_GENERATORS: dict[str, StrategyGenerator] = {
    "ai_only": generate_ai_only_orders,
    "model_only": generate_model_only_orders,
    "consensus": generate_consensus_orders,
    "mean_reversion_floor_w1": generate_mean_reversion_orders,
    "breakout_protected_by_floor": generate_breakout_floor_orders,
    "weekly_opportunity_ridge": generate_weekly_opportunity_orders,
}


def _parse_scalar(value: str):
    raw = value.strip()
    if raw.lower() in {"true", "false"}:
        return raw.lower() == "true"
    try:
        if "." in raw:
            return float(raw)
        return int(raw)
    except ValueError:
        return raw.strip('"').strip("'")


def load_simple_yaml(path: Path) -> dict:
    root: dict = {}
    stack: list[tuple[int, dict]] = [(-1, root)]
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        key, _, value = line.strip().partition(":")
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if value.strip() == "":
            parent[key] = {}
            stack.append((indent, parent[key]))
        else:
            parent[key] = _parse_scalar(value)
    return root


def _load_forecasts(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return payload
    return payload.get("rows", payload.get("dataset_forecasts", []))


def _eligible(row: dict, strategy_cfg: dict) -> bool:
    close = float(row.get("close", 0) or 0)
    min_price = float(strategy_cfg["universe"]["min_price"])
    max_price = float(strategy_cfg["universe"]["max_price"])
    if close < min_price or close > max_price:
        return False
    sectors = str(strategy_cfg["universe"].get("excluded_sectors", "")).split(",")
    excluded = {s.strip() for s in sectors if s.strip()}
    sector = str(row.get("sector", ""))
    return sector not in excluded


def run_strategies(
    forecast_rows: list[dict],
    config: dict,
    session: str,
    cooldown_state: dict[str, int] | None = None,
    current_cycle: int = 0,
    mode: str = "backtest",
) -> dict:
    """Generate strategy orders only for strategies explicitly enabled in ``mode``.

    ``backtest`` remains the research path used by historical tests. ``paper`` and
    ``live`` are fail-closed and require explicit activation flags in config.
    """
    rows = [dict(r) for r in forecast_rows]
    rows_by_symbol = {str(r.get("symbol")): r for r in rows}
    normalized_mode = str(mode or "backtest").strip().lower()
    decisions = activation_snapshot(config, normalized_mode)

    def filt(strategy_id: str) -> list[dict]:
        cfg = config["strategies"][strategy_id]
        return [r for r in rows if _eligible(r, cfg)]

    candidates: list[StrategyDecision] = []
    for strategy_id, generator in STRATEGY_GENERATORS.items():
        decision = decisions.get(strategy_id, {})
        if decision.get("allowed") is not True:
            continue
        strategy_cfg = config["strategies"][strategy_id]
        candidates += generator(filt(strategy_id), config, strategy_cfg, session)

    allocation = allocate_orders(
        candidates,
        rows_by_symbol,
        config,
        cooldown_state=cooldown_state,
        current_cycle=current_cycle,
    )
    m3_influenced_candidates = sum(
        1 for candidate in candidates if (candidate.m3_context or {}).get("enabled") is True
    )
    m3_priority_adjusted_candidates = sum(
        1
        for candidate in candidates
        if int((candidate.m3_context or {}).get("priority_adjustment", 0) or 0) != 0
    )
    return {
        "mode": normalized_mode,
        "activation": decisions,
        "orders": allocation["orders"],
        "blocked": allocation["blocked_collisions"],
        "n_candidates": len(candidates),
        "n_m3_influenced_candidates": m3_influenced_candidates,
        "n_m3_priority_adjusted_candidates": m3_priority_adjusted_candidates,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run registered strategy pack on forecast dataset")
    parser.add_argument("--forecasts", required=True, help="Forecast dataset json path")
    parser.add_argument("--config", default="config/strategies.yaml", help="Strategies config path")
    parser.add_argument("--session", required=True, help="Operational session")
    parser.add_argument("--output", required=True, help="Output JSON path")
    parser.add_argument("--current-cycle", type=int, default=0)
    parser.add_argument(
        "--mode",
        choices=sorted(VALID_MODES),
        default=None,
        help="Activation mode; defaults to activation.default_mode from config",
    )
    args = parser.parse_args()

    cfg = load_simple_yaml(Path(args.config))
    rows = _load_forecasts(Path(args.forecasts))
    configured_default = str(cfg.get("activation", {}).get("default_mode") or "backtest")
    mode = args.mode or configured_default
    out = run_strategies(
        rows,
        cfg,
        args.session,
        cooldown_state={},
        current_cycle=args.current_cycle,
        mode=mode,
    )
    Path(args.output).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
