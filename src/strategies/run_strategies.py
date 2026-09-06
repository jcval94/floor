from __future__ import annotations

import argparse
import json
from pathlib import Path

from strategies.activation import VALID_MODES, activation_snapshot
from strategies.base import StrategyDecision
from strategies.common import platform_fee_bps_per_side, round_trip_cost_bps
from strategies.portfolio_allocator import allocate_orders
from strategies.registry import STRATEGY_GENERATORS


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


def _signal_payload(signal: StrategyDecision, config: dict) -> dict:
    return {
        "strategy_id": signal.strategy_id,
        "symbol": signal.symbol,
        "action": signal.side,
        "score": round(float(signal.score), 8),
        "horizon": signal.horizon,
        "reason": signal.entry_reason,
        "round_trip_cost_bps": round(round_trip_cost_bps(config), 4),
        "platform_fee_bps_per_side": round(platform_fee_bps_per_side(config), 4),
        "m3_context": signal.m3_context or {},
        "priority_adjustment": int(signal.priority_adjustment or 0),
    }


def run_strategies(
    forecast_rows: list[dict],
    config: dict,
    session: str,
    cooldown_state: dict[str, int] | None = None,
    current_cycle: int = 0,
    mode: str = "backtest",
) -> dict:
    """Generate auditable BUY/SELL/HOLD signals and executable BUY/SELL orders."""
    rows = [dict(r) for r in forecast_rows]
    rows_by_symbol = {str(r.get("symbol")): r for r in rows}
    normalized_mode = str(mode or "backtest").strip().lower()
    decisions = activation_snapshot(config, normalized_mode)

    def filt(strategy_id: str) -> list[dict]:
        cfg = config["strategies"][strategy_id]
        return [r for r in rows if _eligible(r, cfg)]

    signals: list[StrategyDecision] = []
    for strategy_id, generator in STRATEGY_GENERATORS.items():
        decision = decisions.get(strategy_id, {})
        if decision.get("allowed") is not True:
            continue
        strategy_cfg = config["strategies"][strategy_id]
        signals += generator(filt(strategy_id), config, strategy_cfg, session)

    trade_candidates = [
        signal
        for signal in signals
        if signal.side in {"BUY", "SELL"} and int(signal.qty) > 0
    ]
    allocation = allocate_orders(
        trade_candidates,
        rows_by_symbol,
        config,
        cooldown_state=cooldown_state,
        current_cycle=current_cycle,
    )

    rt_cost = round_trip_cost_bps(config)
    platform = platform_fee_bps_per_side(config)
    for order in allocation["orders"]:
        order["round_trip_cost_bps"] = rt_cost
        order["platform_fee_bps_per_side"] = platform

    action_counts = {"BUY": 0, "SELL": 0, "HOLD": 0}
    for signal in signals:
        action_counts[signal.side] = action_counts.get(signal.side, 0) + 1

    return {
        "mode": normalized_mode,
        "activation": decisions,
        "signals": [_signal_payload(signal, config) for signal in signals],
        "action_counts": action_counts,
        "orders": allocation["orders"],
        "blocked": allocation["blocked_collisions"],
        "n_signals": len(signals),
        "n_candidates": len(trade_candidates),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run BUY/SELL/HOLD strategy pack on forecast dataset"
    )
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
