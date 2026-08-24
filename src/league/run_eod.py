from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from floor.universe import parse_universe_yaml
from league.engine import (
    advance_league,
    initialize_league,
    load_state,
    sha256_file,
    write_leaderboard,
)
from league.market_features import build_league_market_snapshot
from models.train_weekly_opportunity import predict_weekly_opportunity
from strategies.run_strategies import load_simple_yaml
from strategies.strategy_breakout_floor import generate_breakout_floor_orders
from strategies.strategy_weekly_opportunity import generate_weekly_opportunity_orders


def _load_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Expected JSON object at {path}")
    return payload


def _decision_targets(decisions: list[Any], rows_by_symbol: dict[str, dict], strategies_cfg: dict) -> dict[str, dict]:
    global_nav = float(strategies_cfg.get("portfolio", {}).get("nav_usd", 1.0) or 1.0)
    targets: dict[str, dict] = {}
    for decision in decisions:
        row = rows_by_symbol.get(str(decision.symbol), {})
        close = float(row.get("close", 0.0) or 0.0)
        if close <= 0 or int(decision.qty) <= 0:
            continue
        weight = min(1.0, max(0.0, int(decision.qty) * close / global_nav))
        targets[str(decision.symbol)] = {
            "weight": weight,
            "stop_price": float(decision.stop_price or 0.0),
            "take_profit_price": float(decision.take_profit_price or 0.0),
            "score": float(decision.score),
        }
    return targets


def _strategy_targets(
    rows: list[dict],
    strategies_cfg: dict,
    weekly_artifact: dict,
    *,
    include_weekly: bool,
) -> dict[str, dict[str, dict]]:
    scored = [dict(row) for row in rows]
    params = weekly_artifact.get("params", {})
    if params.get("canonical_serving_enabled") is not False:
        raise RuntimeError("Strategy League refuses Weekly artifact unless canonical_serving_enabled=false")
    for row in scored:
        row["weekly_opportunity_score"] = predict_weekly_opportunity(row, params)
    rows_by_symbol = {str(row.get("symbol")): row for row in scored}

    targets: dict[str, dict[str, dict]] = {}
    if include_weekly:
        cfg = strategies_cfg["strategies"]["weekly_opportunity_ridge"]
        decisions = generate_weekly_opportunity_orders(scored, strategies_cfg, cfg, "CLOSE")
        targets["weekly_opportunity_ridge"] = _decision_targets(decisions, rows_by_symbol, strategies_cfg)

    breakout_cfg = strategies_cfg["strategies"]["breakout_protected_by_floor"]
    breakout = generate_breakout_floor_orders(scored, strategies_cfg, breakout_cfg, "CLOSE")
    targets["breakout_protected_by_floor"] = _decision_targets(breakout, rows_by_symbol, strategies_cfg)
    return targets


def _benchmark_targets(symbols: list[str], spy: str) -> dict[str, dict[str, dict]]:
    equal_weight = 1.0 / max(len(symbols), 1)
    return {
        "benchmark_spy": {spy: {"weight": 1.0}},
        "benchmark_equal_weight": {
            symbol: {"weight": equal_weight} for symbol in sorted(symbols)
        },
    }


def _write_waiting(root: Path, league_cfg: dict, status: str, detail: str) -> dict:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "league_id": league_cfg.get("league_id"),
        "mode": "shadow_paper",
        "status": status,
        "detail": detail,
        "start_session": None,
        "last_session": None,
        "sessions": 0,
        "initial_nav_usd": float(league_cfg.get("initial_nav_usd", 100000.0)),
        "automatic_promotion": False,
        "live_execution_enabled": False,
        "rows": [],
    }
    root.mkdir(parents=True, exist_ok=True)
    (root / "leaderboard.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def run_league_eod(
    data_dir: Path,
    league_config_path: Path,
    strategies_config_path: Path,
    universe_path: Path,
) -> dict:
    league_cfg = _load_json(league_config_path)
    if league_cfg.get("enabled") is not True:
        return _write_waiting(data_dir / "metrics" / "strategy_league", league_cfg, "DISABLED", "league.enabled is not true")
    if str(league_cfg.get("mode")) != "shadow_paper":
        raise RuntimeError("Strategy League only supports shadow_paper mode")

    root = data_dir / "metrics" / "strategy_league"
    model_path = Path(str(league_cfg["weekly_model_path"]))
    if not model_path.is_absolute():
        model_path = league_config_path.parent.parent / model_path
    if not model_path.exists():
        return _write_waiting(root, league_cfg, "WAITING_FOR_WEEKLY_MODEL", f"missing frozen challenger: {model_path}")

    weekly_artifact = _load_json(model_path)
    strategies_cfg = load_simple_yaml(strategies_config_path)
    symbols = parse_universe_yaml(universe_path)
    spy = str(league_cfg.get("benchmark_spy") or "SPY").upper()
    snapshot = build_league_market_snapshot(
        data_dir / "market" / "market_data.sqlite",
        data_dir / "persistence" / "app.sqlite",
        symbols,
        benchmark_symbol=spy,
    )
    session = str(snapshot.get("session") or "")
    league_id = str(league_cfg["league_id"])
    run_dir = root / "runs" / league_id
    state = load_state(run_dir)

    if not session:
        return _write_waiting(root, league_cfg, "WAITING_FOR_MARKET_DATA", "no current SPY market session")
    if state is None and snapshot.get("status") != "OK":
        return _write_waiting(
            root,
            league_cfg,
            "WAITING_FOR_COMPLETE_INPUT",
            f"genesis requires complete current forecasts: {snapshot.get('complete_symbols', [])}",
        )

    frozen_contract = {
        "league_config_sha256": sha256_file(league_config_path),
        "strategies_config_sha256": sha256_file(strategies_config_path),
        "weekly_model_sha256": sha256_file(model_path),
    }

    rows = list(snapshot.get("rows", []))
    next_targets: dict[str, dict[str, dict]] = {}
    if snapshot.get("status") == "OK":
        frequency = max(1, int(league_cfg.get("weekly_review_frequency_sessions", 5)))
        include_weekly = state is None or int(state.get("session_count", 0)) % frequency == 0
        next_targets = _strategy_targets(
            rows,
            strategies_cfg,
            weekly_artifact,
            include_weekly=include_weekly,
        )

    if state is None:
        initial_targets = {
            **next_targets,
            **_benchmark_targets(symbols, spy),
        }
        state = initialize_league(run_dir, league_cfg, session, frozen_contract, initial_targets)
    elif session > str(state.get("last_session") or ""):
        state = advance_league(
            run_dir,
            state,
            league_cfg,
            session,
            dict(snapshot.get("bars", {})),
            frozen_contract,
            next_targets,
        )

    payload = write_leaderboard(run_dir, state, league_cfg)
    root.mkdir(parents=True, exist_ok=True)
    (root / "leaderboard.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    status_payload = {
        "status": payload["status"],
        "league_id": league_id,
        "last_session": payload["last_session"],
        "sessions": payload["sessions"],
        "market_input_status": snapshot.get("status"),
        "live_execution_enabled": False,
        "operational_paper_gateway_used": False,
    }
    (root / "status.json").write_text(json.dumps(status_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Update the prospective Strategy League once at EOD")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--league-config", default="config/strategy_league.json")
    parser.add_argument("--strategies-config", default="config/strategies.yaml")
    parser.add_argument("--universe", default="config/universe.yaml")
    args = parser.parse_args()
    payload = run_league_eod(
        Path(args.data_dir),
        Path(args.league_config),
        Path(args.strategies_config),
        Path(args.universe),
    )
    print(
        json.dumps(
            {
                "status": payload.get("status"),
                "league_id": payload.get("league_id"),
                "sessions": payload.get("sessions"),
                "last_session": payload.get("last_session"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
