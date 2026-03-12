from __future__ import annotations

import argparse
import json
from pathlib import Path

from strategies.portfolio_allocator import allocate_orders
from strategies.strategy_ai_only import generate_ai_only_orders
from strategies.strategy_breakout_floor import generate_breakout_floor_orders
from strategies.strategy_consensus import generate_consensus_orders
from strategies.strategy_mean_reversion import generate_mean_reversion_orders
from strategies.strategy_model_only import generate_model_only_orders


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


def run_strategies(forecast_rows: list[dict], config: dict, session: str, cooldown_state: dict[str, int] | None = None, current_cycle: int = 0) -> dict:
    rows = [dict(r) for r in forecast_rows]
    rows_by_symbol = {str(r.get("symbol")): r for r in rows}

    def filt(strategy_id: str) -> list[dict]:
        cfg = config["strategies"][strategy_id]
        return [r for r in rows if _eligible(r, cfg)]

    candidates = []
    candidates += generate_ai_only_orders(filt("ai_only"), config, config["strategies"]["ai_only"], session)
    candidates += generate_model_only_orders(filt("model_only"), config, config["strategies"]["model_only"], session)
    candidates += generate_consensus_orders(filt("consensus"), config, config["strategies"]["consensus"], session)
    candidates += generate_mean_reversion_orders(filt("mean_reversion_floor_w1"), config, config["strategies"]["mean_reversion_floor_w1"], session)
    candidates += generate_breakout_floor_orders(filt("breakout_protected_by_floor"), config, config["strategies"]["breakout_protected_by_floor"], session)

    allocation = allocate_orders(candidates, rows_by_symbol, config, cooldown_state=cooldown_state, current_cycle=current_cycle)
    return {
        "orders": allocation["orders"],
        "blocked": allocation["blocked_collisions"],
        "n_candidates": len(candidates),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run 5 strategy pack on forecast dataset")
    parser.add_argument("--forecasts", required=True, help="Forecast dataset json path")
    parser.add_argument("--config", default="config/strategies.yaml", help="Strategies config path")
    parser.add_argument("--session", required=True, help="Operational session")
    parser.add_argument("--output", required=True, help="Output JSON path")
    parser.add_argument("--current-cycle", type=int, default=0)
    args = parser.parse_args()

    cfg = load_simple_yaml(Path(args.config))
    rows = _load_forecasts(Path(args.forecasts))
    out = run_strategies(rows, cfg, args.session, cooldown_state={}, current_cycle=args.current_cycle)
    Path(args.output).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
