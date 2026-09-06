from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from floor.universe import parse_universe_yaml
from league.capital_challenger import build_capital_challenger_targets
from league.engine import (
    advance_league,
    initialize_league,
    load_state,
    sha256_file,
    write_leaderboard,
)
from league.market_features import build_league_market_snapshot
from models.train_weekly_opportunity import predict_weekly_opportunity
from strategies.breakout_protected_by_floor import generate_breakout_floor_orders
from strategies.cross_horizon_asymmetry import generate_cross_horizon_orders
from strategies.mean_reversion_floor_w1 import generate_mean_reversion_orders
from strategies.run_strategies import load_simple_yaml
from strategies.weekly_opportunity_ridge import generate_weekly_opportunity_orders


def _load_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Expected JSON object at {path}")
    return payload


def _decision_targets(
    decisions: list[Any],
    rows_by_symbol: dict[str, dict],
    strategies_cfg: dict,
) -> dict[str, dict]:
    """Translate only BUY signals into long-only shadow-paper targets.

    SELL means do not own / exit on the next rebalance; HOLD creates no new
    target. This keeps Strategy League long-only while the research layer can
    still evaluate symmetric BUY/SELL/HOLD recommendations.
    """
    global_nav = float(strategies_cfg.get("portfolio", {}).get("nav_usd", 1.0) or 1.0)
    targets: dict[str, dict] = {}
    for decision in decisions:
        if str(getattr(decision, "side", "")).upper() != "BUY":
            continue
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


def _equal_weight_capped_targets(
    targets: dict[str, dict],
    max_weight: float,
) -> dict[str, dict]:
    """Apply the research contract min(1 / n_selected, max_weight)."""
    if not targets:
        return {}
    cap = max(0.0, min(1.0, float(max_weight)))
    weight = min(1.0 / len(targets), cap)
    normalized = {
        symbol: {**spec, "weight": weight}
        for symbol, spec in targets.items()
    }
    gross = sum(float(spec["weight"]) for spec in normalized.values())
    if gross > 1.0 + 1e-9:
        raise RuntimeError(f"Weekly target gross exposure exceeds 100%: {gross:.8f}")
    return normalized


def _strategy_targets(
    rows: list[dict],
    strategies_cfg: dict,
    weekly_artifact: dict,
    *,
    include_weekly: bool,
    include_mean_reversion: bool = False,
    include_cross_horizon: bool = False,
    include_challenger: bool = False,
    challenger_cfg: dict | None = None,
) -> dict[str, dict[str, dict]]:
    challenger_cfg = challenger_cfg or {}
    scored = [dict(row) for row in rows]
    params = weekly_artifact.get("params", {})
    if params.get("canonical_serving_enabled") is not False:
        raise RuntimeError(
            "Strategy League refuses Weekly artifact unless canonical_serving_enabled=false"
        )
    for row in scored:
        row["weekly_opportunity_score"] = predict_weekly_opportunity(row, params)
    rows_by_symbol = {str(row.get("symbol")): row for row in scored}

    targets: dict[str, dict[str, dict]] = {}
    weekly_decisions: list[Any] = []
    if include_weekly or include_challenger:
        weekly_cfg = strategies_cfg["strategies"]["weekly_opportunity_ridge"]
        weekly_decisions = generate_weekly_opportunity_orders(
            scored,
            strategies_cfg,
            weekly_cfg,
            "CLOSE",
        )
        if include_weekly:
            raw_targets = _decision_targets(
                weekly_decisions,
                rows_by_symbol,
                strategies_cfg,
            )
            max_weight = float(
                weekly_cfg.get("position_sizing", {}).get("max_weight_pct_nav", 0.20)
                or 0.20
            )
            targets["weekly_opportunity_ridge"] = _equal_weight_capped_targets(
                raw_targets,
                max_weight,
            )

    breakout_cfg = strategies_cfg["strategies"]["breakout_protected_by_floor"]
    breakout = generate_breakout_floor_orders(
        scored,
        strategies_cfg,
        breakout_cfg,
        "CLOSE",
    )
    targets["breakout_protected_by_floor"] = _decision_targets(
        breakout,
        rows_by_symbol,
        strategies_cfg,
    )

    mean_reversion: list[Any] = []
    if include_mean_reversion or include_challenger:
        mean_cfg = strategies_cfg["strategies"]["mean_reversion_floor_w1"]
        mean_reversion = generate_mean_reversion_orders(
            scored,
            strategies_cfg,
            mean_cfg,
            "CLOSE",
        )
        if include_mean_reversion:
            targets["mean_reversion_floor_w1"] = _decision_targets(
                mean_reversion,
                rows_by_symbol,
                strategies_cfg,
            )

    cross_horizon: list[Any] = []
    if include_cross_horizon or include_challenger:
        cross_cfg = strategies_cfg["strategies"]["cross_horizon_asymmetry"]
        cross_horizon = generate_cross_horizon_orders(
            scored,
            strategies_cfg,
            cross_cfg,
            "CLOSE",
        )
        if include_cross_horizon:
            targets["cross_horizon_asymmetry"] = _decision_targets(
                cross_horizon,
                rows_by_symbol,
                strategies_cfg,
            )

    if include_challenger:
        targets["capital_allocation_challenger"] = build_capital_challenger_targets(
            {
                "weekly_opportunity_ridge": weekly_decisions,
                "breakout_protected_by_floor": breakout,
                "mean_reversion_floor_w1": mean_reversion,
                "cross_horizon_asymmetry": cross_horizon,
            },
            rows_by_symbol,
            strategies_cfg,
            challenger_cfg,
        )

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
        "initial_nav_usd": float(league_cfg.get("initial_nav_usd", 10000.0)),
        "automatic_promotion": False,
        "live_execution_enabled": False,
        "rows": [],
    }
    root.mkdir(parents=True, exist_ok=True)
    (root / "leaderboard.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return payload


def _holding_sessions(strategy_cfg: dict, default: int) -> int:
    value = int(
        strategy_cfg.get("exits", {}).get("temporal_exit_business_days", default)
        or default
    )
    if value <= 0:
        raise RuntimeError("Strategy League max holding sessions must be positive")
    return value


def run_league_eod(
    data_dir: Path,
    league_config_path: Path,
    strategies_config_path: Path,
    universe_path: Path,
) -> dict:
    league_cfg = _load_json(league_config_path)
    if league_cfg.get("enabled") is not True:
        return _write_waiting(
            data_dir / "metrics" / "strategy_league",
            league_cfg,
            "DISABLED",
            "league.enabled is not true",
        )
    if str(league_cfg.get("mode")) != "shadow_paper":
        raise RuntimeError("Strategy League only supports shadow_paper mode")

    root = data_dir / "metrics" / "strategy_league"
    model_path = Path(str(league_cfg["weekly_model_path"]))
    if not model_path.is_absolute():
        model_path = league_config_path.parent.parent / model_path
    if not model_path.exists():
        return _write_waiting(
            root,
            league_cfg,
            "WAITING_FOR_WEEKLY_MODEL",
            f"missing frozen challenger: {model_path}",
        )

    weekly_artifact = _load_json(model_path)
    strategies_cfg = load_simple_yaml(strategies_config_path)
    strategy_configs = strategies_cfg["strategies"]
    weekly_cfg = strategy_configs["weekly_opportunity_ridge"]
    mean_cfg = strategy_configs["mean_reversion_floor_w1"]
    cross_cfg = strategy_configs["cross_horizon_asymmetry"]

    weekly_max_holding_sessions = _holding_sessions(weekly_cfg, 10)
    mean_max_holding_sessions = _holding_sessions(mean_cfg, 5)
    cross_max_holding_sessions = _holding_sessions(cross_cfg, 10)

    challenger_cfg = dict(league_cfg.get("capital_allocation_challenger", {}))
    challenger_max_holding_sessions = int(
        challenger_cfg.get("max_holding_sessions", 10) or 10
    )
    if challenger_max_holding_sessions <= 0:
        raise RuntimeError("Capital challenger max holding sessions must be positive")

    runtime_league_cfg = {
        **league_cfg,
        "strategy_max_holding_sessions": {
            "weekly_opportunity_ridge": weekly_max_holding_sessions,
            "mean_reversion_floor_w1": mean_max_holding_sessions,
            "cross_horizon_asymmetry": cross_max_holding_sessions,
            "capital_allocation_challenger": challenger_max_holding_sessions,
        },
    }

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
        return _write_waiting(
            root,
            league_cfg,
            "WAITING_FOR_MARKET_DATA",
            "no current SPY market session",
        )
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
        weekly_frequency = max(
            1,
            int(league_cfg.get("weekly_review_frequency_sessions", 5)),
        )
        mean_frequency = mean_max_holding_sessions
        cross_frequency = cross_max_holding_sessions
        challenger_frequency = max(
            1,
            int(challenger_cfg.get("review_frequency_sessions", weekly_frequency)),
        )
        current_count = int(state.get("session_count", 0)) if state is not None else 0
        include_weekly = state is None or current_count % weekly_frequency == 0
        include_mean_reversion = state is None or current_count % mean_frequency == 0
        include_cross_horizon = state is None or current_count % cross_frequency == 0
        include_challenger = state is None or current_count % challenger_frequency == 0
        next_targets = _strategy_targets(
            rows,
            strategies_cfg,
            weekly_artifact,
            include_weekly=include_weekly,
            include_mean_reversion=include_mean_reversion,
            include_cross_horizon=include_cross_horizon,
            include_challenger=include_challenger,
            challenger_cfg=challenger_cfg,
        )

    if state is None:
        initial_targets = {
            **next_targets,
            **_benchmark_targets(symbols, spy),
        }
        state = initialize_league(
            run_dir,
            runtime_league_cfg,
            session,
            frozen_contract,
            initial_targets,
        )
    elif session > str(state.get("last_session") or ""):
        state = advance_league(
            run_dir,
            state,
            runtime_league_cfg,
            session,
            dict(snapshot.get("bars", {})),
            frozen_contract,
            next_targets,
        )

    payload = write_leaderboard(run_dir, state, runtime_league_cfg)
    root.mkdir(parents=True, exist_ok=True)
    (root / "leaderboard.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    status_payload = {
        "status": payload["status"],
        "league_id": league_id,
        "last_session": payload["last_session"],
        "sessions": payload["sessions"],
        "market_input_status": snapshot.get("status"),
        "live_execution_enabled": False,
        "operational_paper_gateway_used": False,
        "weekly_max_holding_sessions": weekly_max_holding_sessions,
        "mean_reversion_max_holding_sessions": mean_max_holding_sessions,
        "cross_horizon_max_holding_sessions": cross_max_holding_sessions,
        "capital_challenger_max_holding_sessions": challenger_max_holding_sessions,
        "platform_fee_bps_per_side": float(
            league_cfg.get("execution", {}).get("platform_fee_bps_per_side", 0.0)
        ),
    }
    (root / "status.json").write_text(
        json.dumps(status_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Update the prospective Strategy League once at EOD"
    )
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
