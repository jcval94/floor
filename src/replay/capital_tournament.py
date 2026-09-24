from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from floor.universe import parse_universe_yaml
from forecasting.run_forecast import run_forecast_pipeline
from league.engine import (
    advance_league,
    initialize_league,
    sha256_file,
    write_leaderboard,
)
from league.market_features import _feature_row
from league.run_eod import _benchmark_targets, _holding_sessions, _strategy_targets
from replay.historical_close import build_historical_close_feature_rows
from replay.point_in_time import _session_date, group_by_symbol
from replay.runner import (
    _bar_for_day,
    _bars_through,
    _enrich_league_row,
    _load_json,
    _sessions,
)
from replay.yahoo_source import fetch_replay_daily_market_data
from strategies.run_strategies import load_simple_yaml


def _select_complete_session_run(
    *,
    requested_sessions: list[date],
    daily_by_symbol: dict[str, list[dict[str, Any]]],
    symbols: list[str],
    benchmark_symbol: str = "SPY",
    min_sessions: int = 21,
) -> tuple[list[date], dict[str, Any]]:
    """Select the longest contiguous requested run with complete daily bars.

    A retrospective tournament must never silently bridge over a missing market
    session: doing so could skip stops, exits, or rebalance opportunities. When
    a provider omits one or more daily bars, keep the longest contiguous block
    where every strategy symbol and the benchmark have exactly one bar.
    """

    if not requested_sessions:
        raise RuntimeError("retrospective tournament has no requested sessions")

    requested = list(requested_sessions)
    requested_set = set(requested)
    required_symbols = sorted(
        set([*(symbol.upper() for symbol in symbols), benchmark_symbol.upper()])
    )
    complete_by_symbol: dict[str, set[date]] = {}
    missing_by_symbol: dict[str, list[str]] = {}

    for symbol in required_symbols:
        counts: dict[date, int] = {}
        for row in daily_by_symbol.get(symbol, []):
            session_day = _session_date(row.get("timestamp"))
            if session_day in requested_set:
                counts[session_day] = counts.get(session_day, 0) + 1
        complete = {session_day for session_day, count in counts.items() if count == 1}
        complete_by_symbol[symbol] = complete
        missing_by_symbol[symbol] = [
            session_day.isoformat()
            for session_day in requested
            if session_day not in complete
        ]

    common_complete = set(requested)
    for complete in complete_by_symbol.values():
        common_complete &= complete

    runs: list[list[date]] = []
    current: list[date] = []
    for session_day in requested:
        if session_day in common_complete:
            current.append(session_day)
            continue
        if current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)

    if not runs:
        raise RuntimeError("no common complete daily-bar sessions in requested window")

    # Prefer the longest complete run; on ties prefer the most recent segment.
    selected = max(runs, key=lambda run: (len(run), run[-1]))
    required_min = min(max(int(min_sessions), 1), len(requested))
    if len(selected) < required_min:
        raise RuntimeError(
            "insufficient contiguous complete sessions for retrospective tournament: "
            f"selected={len(selected)} required={required_min}"
        )

    selected_set = set(selected)
    incomplete = [
        session_day.isoformat()
        for session_day in requested
        if session_day not in common_complete
    ]
    trimmed_complete = [
        session_day.isoformat()
        for session_day in requested
        if session_day in common_complete and session_day not in selected_set
    ]
    audit = {
        "requested_start_session": requested[0].isoformat(),
        "requested_end_session": requested[-1].isoformat(),
        "requested_sessions": len(requested),
        "effective_start_session": selected[0].isoformat(),
        "effective_end_session": selected[-1].isoformat(),
        "effective_sessions": len(selected),
        "common_session_coverage": len(selected) / len(requested),
        "incomplete_sessions": incomplete,
        "trimmed_complete_sessions": trimmed_complete,
        "missing_sessions_by_symbol": {
            symbol: sessions
            for symbol, sessions in missing_by_symbol.items()
            if sessions
        },
        "selection_rule": "longest_contiguous_common_complete_daily_bar_run",
    }
    return selected, audit


def run_capital_tournament(
    *,
    start: date,
    end: date,
    output_dir: Path,
    universe_path: Path = Path("config/universe.yaml"),
    model_registry_dir: Path = Path("data/training/models"),
    league_config_path: Path = Path("config/strategy_league.json"),
    strategies_config_path: Path = Path("config/strategies.yaml"),
    weekly_model_path: Path | None = None,
) -> dict[str, Any]:
    """Run a CLOSE-only PIT tournament for fast allocator research.

    This is retrospective diagnostic evidence. It uses completed daily bars
    at each historical CLOSE checkpoint plus the Strategy League execution
    engine, so older sessions do not depend on Yahoo's short intraday retention
    window. All members still see the same point-in-time bars, next-open
    execution, costs and conservative stop-before-take exit ordering.
    """

    requested_sessions = _sessions(start, end)
    symbols = parse_universe_yaml(universe_path)
    daily_rows, market_summary = fetch_replay_daily_market_data(symbols)
    daily_by_symbol = group_by_symbol(daily_rows)
    sessions, session_selection = _select_complete_session_run(
        requested_sessions=requested_sessions,
        daily_by_symbol=daily_by_symbol,
        symbols=symbols,
        benchmark_symbol="SPY",
    )
    market_summary = {
        **market_summary,
        "session_selection": session_selection,
    }

    league_cfg = _load_json(league_config_path)
    if weekly_model_path is None:
        configured_weekly_model = str(league_cfg.get("weekly_model_path") or "").strip()
        if not configured_weekly_model:
            raise ValueError(
                f"weekly_model_path missing from league config: {league_config_path}"
            )
        weekly_model_path = Path(configured_weekly_model)
    strategies_cfg = load_simple_yaml(strategies_config_path)
    weekly_artifact = _load_json(weekly_model_path)
    challenger_cfg = dict(league_cfg.get("capital_allocation_challenger", {}))

    strategy_configs = strategies_cfg["strategies"]
    weekly_cfg = strategy_configs["weekly_opportunity_ridge"]
    mean_cfg = strategy_configs["mean_reversion_floor_w1"]
    cross_cfg = strategy_configs["cross_horizon_asymmetry"]
    weekly_max_holding = _holding_sessions(weekly_cfg, 10)
    mean_max_holding = _holding_sessions(mean_cfg, 5)
    cross_max_holding = _holding_sessions(cross_cfg, 10)
    challenger_max_holding = int(challenger_cfg.get("max_holding_sessions", 10) or 10)

    league_cfg = {
        **league_cfg,
        "league_id": (
            f"capital_tournament_{sessions[0].strftime('%Y%m%d')}_"
            f"{sessions[-1].strftime('%Y%m%d')}"
        ),
        "strategy_max_holding_sessions": {
            "weekly_opportunity_ridge": weekly_max_holding,
            "mean_reversion_floor_w1": mean_max_holding,
            "cross_horizon_asymmetry": cross_max_holding,
            "capital_allocation_challenger": challenger_max_holding,
        },
    }

    frozen_contract = {
        "league_config_sha256": sha256_file(league_config_path),
        "strategies_config_sha256": sha256_file(strategies_config_path),
        "weekly_model_sha256": sha256_file(weekly_model_path),
    }
    run_dir = output_dir / "runs" / str(league_cfg["league_id"])
    state: dict[str, Any] | None = None
    spy_rows = daily_by_symbol["SPY"]
    audits: list[dict[str, Any]] = []

    weekly_frequency = max(
        1,
        int(league_cfg.get("weekly_review_frequency_sessions", 5)),
    )
    mean_frequency = mean_max_holding
    cross_frequency = cross_max_holding
    challenger_frequency = max(
        1,
        int(challenger_cfg.get("review_frequency_sessions", weekly_frequency)),
    )

    for session_day in sessions:
        session = session_day.isoformat()
        pit_rows, audit = build_historical_close_feature_rows(
            daily_by_symbol=daily_by_symbol,
            symbols=symbols,
            benchmark_symbol="SPY",
            session_day=session_day,
        )
        audits.append(dict(audit))
        if audit.get("future_data_used") is not False:
            raise RuntimeError(f"future data detected in tournament session={session}")

        as_of = datetime.fromisoformat(str(audit["checkpoint"]))
        generated = run_forecast_pipeline(
            market_rows=pit_rows,
            ai_by_symbol={},
            session="CLOSE",
            as_of=as_of,
            model_registry_dir=model_registry_dir,
        )
        forecasts_list = list(generated["dataset_forecasts"])
        blocked = list(generated["blocked_list"])
        if blocked:
            raise RuntimeError(
                f"capital tournament produced blocked forecasts session={session}: {blocked[:5]}"
            )
        if len(forecasts_list) != len(symbols):
            raise RuntimeError(
                f"capital tournament incomplete forecasts session={session} "
                f"got={len(forecasts_list)} expected={len(symbols)}"
            )
        forecasts = {
            str(row["symbol"]).upper(): row for row in forecasts_list
        }

        bars = {
            symbol: bar
            for symbol in [*symbols, "SPY"]
            if (bar := _bar_for_day(daily_by_symbol.get(symbol, []), session_day))
            is not None
        }
        if len(bars) != len(symbols) + 1:
            missing = sorted(set([*symbols, "SPY"]) - set(bars))
            raise RuntimeError(
                f"capital tournament missing daily bars session={session}: {missing}"
            )

        spy_history = _bars_through(spy_rows, session_day)
        feature_rows: list[dict[str, Any]] = []
        for symbol in symbols:
            feature = _feature_row(
                symbol,
                _bars_through(daily_by_symbol.get(symbol, []), session_day),
                spy_history,
            )
            forecast = forecasts.get(symbol)
            if feature is None or forecast is None:
                raise RuntimeError(
                    f"capital tournament incomplete league row session={session} symbol={symbol}"
                )
            feature_rows.append(_enrich_league_row(feature, forecast))

        current_count = int(state.get("session_count", 0)) if state is not None else 0
        include_weekly = state is None or current_count % weekly_frequency == 0
        include_mean_reversion = state is None or current_count % mean_frequency == 0
        include_cross_horizon = state is None or current_count % cross_frequency == 0
        include_challenger = state is None or current_count % challenger_frequency == 0
        next_targets = _strategy_targets(
            feature_rows,
            strategies_cfg,
            weekly_artifact,
            include_weekly=include_weekly,
            include_mean_reversion=include_mean_reversion,
            include_cross_horizon=include_cross_horizon,
            include_challenger=include_challenger,
            challenger_cfg=challenger_cfg,
        )

        if state is None:
            state = initialize_league(
                run_dir,
                league_cfg,
                session,
                frozen_contract,
                {
                    **next_targets,
                    **_benchmark_targets(symbols, "SPY"),
                },
            )
        else:
            state = advance_league(
                run_dir,
                state,
                league_cfg,
                session,
                bars,
                frozen_contract,
                next_targets,
            )

    if state is None:
        raise RuntimeError("capital tournament produced no league state")

    leaderboard = write_leaderboard(run_dir, state, league_cfg)
    output_dir.mkdir(parents=True, exist_ok=True)
    result = {
        "schema_version": 1,
        "evidence_type": "retrospective_point_in_time_capital_tournament",
        "prospective_evidence": False,
        "requested_start_session": requested_sessions[0].isoformat(),
        "requested_end_session": requested_sessions[-1].isoformat(),
        "requested_sessions": len(requested_sessions),
        "start_session": sessions[0].isoformat(),
        "end_session": sessions[-1].isoformat(),
        "sessions": len(sessions),
        "initial_nav_usd": float(league_cfg["initial_nav_usd"]),
        "market_source": market_summary,
        "future_data_used": any(bool(row.get("future_data_used")) for row in audits),
        "challenger_config": challenger_cfg,
        "leaderboard": leaderboard,
    }
    (output_dir / "capital_tournament.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run retrospective PIT Strategy League tournament for capital allocation"
    )
    parser.add_argument("--start", default="2026-06-22")
    parser.add_argument("--end", default="2026-09-23")
    parser.add_argument("--output", required=True)
    parser.add_argument("--universe", default="config/universe.yaml")
    parser.add_argument("--model-registry", default="data/training/models")
    parser.add_argument("--league-config", default="config/strategy_league.json")
    parser.add_argument("--strategies-config", default="config/strategies.yaml")
    parser.add_argument("--weekly-model", default=None)
    args = parser.parse_args()

    result = run_capital_tournament(
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
        output_dir=Path(args.output),
        universe_path=Path(args.universe),
        model_registry_dir=Path(args.model_registry),
        league_config_path=Path(args.league_config),
        strategies_config_path=Path(args.strategies_config),
        weekly_model_path=Path(args.weekly_model) if args.weekly_model else None,
    )
    rows = result["leaderboard"]["rows"]
    print(
        json.dumps(
            {
                "sessions": result["sessions"],
                "initial_nav_usd": result["initial_nav_usd"],
                "rows": [
                    {
                        "strategy": row["strategy"],
                        "return": row["return"],
                        "sharpe": row["sharpe"],
                        "max_drawdown": row["max_drawdown"],
                        "trades": row["trades"],
                        "costs_paid": row["costs_paid"],
                    }
                    for row in rows
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
