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
from league.run_eod import _benchmark_targets, _strategy_targets
from replay.point_in_time import build_point_in_time_feature_rows, group_by_symbol
from replay.runner import (
    _bar_for_day,
    _bars_through,
    _enrich_league_row,
    _load_json,
    _sessions,
)
from replay.yahoo_source import fetch_replay_market_data
from strategies.run_strategies import load_simple_yaml


def run_capital_tournament(
    *,
    start: date,
    end: date,
    output_dir: Path,
    universe_path: Path = Path("config/universe.yaml"),
    model_registry_dir: Path = Path("data/training/models"),
    league_config_path: Path = Path("config/strategy_league.json"),
    strategies_config_path: Path = Path("config/strategies.yaml"),
    weekly_model_path: Path = Path(
        "data/metrics/strategy_league/models/weekly_opportunity_challenger.json"
    ),
) -> dict[str, Any]:
    """Run a CLOSE-only PIT tournament for fast allocator research.

    This is retrospective diagnostic evidence. It intentionally reuses the
    production replay's PIT feature builder and the Strategy League execution
    engine so all members see the same bars, next-open execution, costs and
    conservative stop-before-take exit ordering.
    """

    sessions = _sessions(start, end)
    symbols = parse_universe_yaml(universe_path)
    daily_rows, intraday_rows, market_summary = fetch_replay_market_data(symbols)
    daily_by_symbol = group_by_symbol(daily_rows)
    intraday_by_symbol = group_by_symbol(intraday_rows)

    league_cfg = _load_json(league_config_path)
    strategies_cfg = load_simple_yaml(strategies_config_path)
    weekly_artifact = _load_json(weekly_model_path)
    challenger_cfg = dict(league_cfg.get("capital_allocation_challenger", {}))

    weekly_cfg = strategies_cfg["strategies"]["weekly_opportunity_ridge"]
    weekly_max_holding = int(
        weekly_cfg.get("exits", {}).get("temporal_exit_business_days", 10) or 10
    )
    challenger_max_holding = int(challenger_cfg.get("max_holding_sessions", 10) or 10)
    league_cfg = {
        **league_cfg,
        "league_id": (
            f"capital_tournament_{sessions[0].strftime('%Y%m%d')}_"
            f"{sessions[-1].strftime('%Y%m%d')}"
        ),
        "strategy_max_holding_sessions": {
            "weekly_opportunity_ridge": weekly_max_holding,
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
    challenger_frequency = max(
        1,
        int(challenger_cfg.get("review_frequency_sessions", weekly_frequency)),
    )

    for session_day in sessions:
        session = session_day.isoformat()
        pit_rows, audit = build_point_in_time_feature_rows(
            daily_by_symbol=daily_by_symbol,
            intraday_by_symbol=intraday_by_symbol,
            symbols=symbols,
            benchmark_symbol="SPY",
            session_day=session_day,
            event="CLOSE",
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
        include_challenger = state is None or current_count % challenger_frequency == 0
        next_targets = _strategy_targets(
            feature_rows,
            strategies_cfg,
            weekly_artifact,
            include_weekly=include_weekly,
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
    parser.add_argument("--start", default="2026-08-24")
    parser.add_argument("--end", default="2026-09-04")
    parser.add_argument("--output", required=True)
    parser.add_argument("--universe", default="config/universe.yaml")
    parser.add_argument("--model-registry", default="data/training/models")
    parser.add_argument("--league-config", default="config/strategy_league.json")
    parser.add_argument("--strategies-config", default="config/strategies.yaml")
    parser.add_argument(
        "--weekly-model",
        default="data/metrics/strategy_league/models/weekly_opportunity_challenger.json",
    )
    args = parser.parse_args()

    result = run_capital_tournament(
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
        output_dir=Path(args.output),
        universe_path=Path(args.universe),
        model_registry_dir=Path(args.model_registry),
        league_config_path=Path(args.league_config),
        strategies_config_path=Path(args.strategies_config),
        weekly_model_path=Path(args.weekly_model),
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
