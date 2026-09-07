from __future__ import annotations

import argparse
import json
import math
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from floor.universe import parse_universe_yaml
from forecasting.run_forecast import run_forecast_pipeline
from league.engine import advance_league, initialize_league, sha256_file, write_leaderboard
from league.freeze import verify_challenger_freeze
from league.market_features import _feature_row
from league.run_eod import _benchmark_targets, _holding_sessions, _strategy_targets
from replay.historical_close import build_historical_close_feature_rows
from replay.point_in_time import _session_date, group_by_symbol
from replay.runner import _bar_for_day, _bars_through, _enrich_league_row, _load_json
from reporting.retrain_backtest_report import (
    build_pre_holdout_training_payload,
    train_evaluation_suite,
)
from storage.market_db import load_daily_bars
from strategies.run_strategies import load_simple_yaml


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _returns(points: list[dict[str, Any]]) -> list[float]:
    navs = [float(point["nav"]) for point in points]
    return [navs[i] / navs[i - 1] - 1.0 for i in range(1, len(navs)) if navs[i - 1] > 0]


def _sharpe(points: list[dict[str, Any]]) -> float | None:
    values = _returns(points)
    if len(values) < 2:
        return None
    mean = _mean(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    std = math.sqrt(max(variance, 0.0))
    return mean / std * math.sqrt(252.0) if std > 1e-12 else None


def _max_drawdown(points: list[dict[str, Any]]) -> float:
    peak = 0.0
    worst = 0.0
    for point in points:
        nav = float(point["nav"])
        peak = max(peak, nav)
        if peak > 0:
            worst = min(worst, nav / peak - 1.0)
    return worst


def _session_dates(
    daily_by_symbol: dict[str, list[dict[str, Any]]],
    start: date,
    end: date,
    benchmark: str = "SPY",
) -> list[date]:
    sessions = sorted(
        {
            _session_date(row["timestamp"])
            for row in daily_by_symbol.get(benchmark, [])
            if start <= _session_date(row["timestamp"]) <= end
        }
    )
    if not sessions:
        raise RuntimeError("walk-forward window has no benchmark sessions")
    return sessions


def _fold_sessions(sessions: list[date], fold_size: int) -> list[list[date]]:
    if fold_size < 5:
        raise ValueError("fold_sessions must be >= 5")
    folds = [sessions[i : i + fold_size] for i in range(0, len(sessions), fold_size)]
    if len(folds) > 1 and len(folds[-1]) < 5:
        folds[-2].extend(folds[-1])
        folds.pop()
    return folds


def _training_cutoff_audit(training_payload: dict[str, Any], fold_start: date) -> dict[str, Any]:
    rows = list(training_payload.get("rows", []))
    observed = [date.fromisoformat(str(row["timestamp"])[:10]) for row in rows]
    target_ends = [
        date.fromisoformat(str(row["target_end_date_m3"])[:10])
        for row in rows
        if row.get("target_end_date_m3") not in (None, "")
    ]
    max_observed = max(observed) if observed else None
    max_target_end = max(target_ends) if target_ends else None
    ok = (
        max_observed is not None
        and max_target_end is not None
        and max_observed < fold_start
        and max_target_end < fold_start
    )
    if not ok:
        raise RuntimeError(
            "walk-forward training purge failed: "
            f"fold_start={fold_start} max_observed={max_observed} max_target_end={max_target_end}"
        )
    return {
        "fold_start": fold_start.isoformat(),
        "training_rows": len(rows),
        "max_training_observation": max_observed.isoformat(),
        "max_training_target_end_m3": max_target_end.isoformat(),
        "training_observation_before_fold": True,
        "training_target_maturity_before_fold": True,
    }


def _run_fold_tournament(
    *,
    sessions: list[date],
    daily_by_symbol: dict[str, list[dict[str, Any]]],
    output_dir: Path,
    universe_path: Path,
    model_registry_dir: Path,
    weekly_model_path: Path,
    league_config_path: Path,
    strategies_config_path: Path,
) -> dict[str, Any]:
    symbols = parse_universe_yaml(universe_path)
    league_cfg = _load_json(league_config_path)
    strategies_cfg = load_simple_yaml(strategies_config_path)
    weekly_artifact = _load_json(weekly_model_path)
    challenger_cfg = dict(league_cfg.get("capital_allocation_challenger", {}))

    strategy_configs = strategies_cfg["strategies"]
    weekly_max_holding = _holding_sessions(strategy_configs["weekly_opportunity_ridge"], 10)
    mean_max_holding = _holding_sessions(strategy_configs["mean_reversion_floor_w1"], 5)
    cross_max_holding = _holding_sessions(strategy_configs["cross_horizon_asymmetry"], 10)
    challenger_max_holding = int(challenger_cfg.get("max_holding_sessions", 10) or 10)
    runtime_cfg = {
        **league_cfg,
        "league_id": f"walk_forward_{sessions[0]:%Y%m%d}_{sessions[-1]:%Y%m%d}",
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
        "d1_model_sha256": sha256_file(model_registry_dir / "d1_champion.json"),
        "w1_model_sha256": sha256_file(model_registry_dir / "w1_champion.json"),
        "q1_model_sha256": sha256_file(model_registry_dir / "q1_champion.json"),
        "value_model_sha256": sha256_file(model_registry_dir / "value_champion.json"),
        "timing_model_sha256": sha256_file(model_registry_dir / "timing_champion.json"),
    }
    run_dir = output_dir / "league"
    state: dict[str, Any] | None = None
    audits: list[dict[str, Any]] = []
    spy_rows = daily_by_symbol["SPY"]

    weekly_frequency = max(1, int(runtime_cfg.get("weekly_review_frequency_sessions", 5)))
    challenger_frequency = max(1, int(challenger_cfg.get("review_frequency_sessions", weekly_frequency)))

    for session_day in sessions:
        session = session_day.isoformat()
        pit_rows, audit = build_historical_close_feature_rows(
            daily_by_symbol=daily_by_symbol,
            symbols=symbols,
            benchmark_symbol="SPY",
            session_day=session_day,
        )
        audits.append(audit)
        if audit.get("future_data_used") is not False:
            raise RuntimeError(f"future market data detected fold session={session}")

        as_of = datetime.fromisoformat(str(audit["checkpoint"]))
        generated = run_forecast_pipeline(
            market_rows=pit_rows,
            ai_by_symbol={},
            session="CLOSE",
            as_of=as_of,
            model_registry_dir=model_registry_dir,
        )
        blocked = list(generated["blocked_list"])
        forecasts_list = list(generated["dataset_forecasts"])
        if blocked or len(forecasts_list) != len(symbols):
            raise RuntimeError(
                f"walk-forward forecast incomplete session={session} blocked={blocked[:3]} "
                f"rows={len(forecasts_list)} expected={len(symbols)}"
            )
        forecasts = {str(row["symbol"]).upper(): row for row in forecasts_list}
        bars = {
            symbol: bar
            for symbol in [*symbols, "SPY"]
            if (bar := _bar_for_day(daily_by_symbol.get(symbol, []), session_day)) is not None
        }
        if len(bars) != len(symbols) + 1:
            missing = sorted(set([*symbols, "SPY"]) - set(bars))
            raise RuntimeError(f"walk-forward missing bars session={session}: {missing}")

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
                raise RuntimeError(f"walk-forward incomplete league row {session} {symbol}")
            feature_rows.append(_enrich_league_row(feature, forecast))

        count = int(state.get("session_count", 0)) if state is not None else 0
        next_targets = _strategy_targets(
            feature_rows,
            strategies_cfg,
            weekly_artifact,
            include_weekly=state is None or count % weekly_frequency == 0,
            include_mean_reversion=state is None or count % mean_max_holding == 0,
            include_cross_horizon=state is None or count % cross_max_holding == 0,
            include_challenger=state is None or count % challenger_frequency == 0,
            challenger_cfg=challenger_cfg,
        )
        if state is None:
            state = initialize_league(
                run_dir,
                runtime_cfg,
                session,
                frozen_contract,
                {**next_targets, **_benchmark_targets(symbols, "SPY")},
            )
        else:
            state = advance_league(
                run_dir,
                state,
                runtime_cfg,
                session,
                bars,
                frozen_contract,
                next_targets,
            )

    if state is None:
        raise RuntimeError("walk-forward fold produced no league state")
    leaderboard = write_leaderboard(run_dir, state, runtime_cfg)
    return {
        "start_session": sessions[0].isoformat(),
        "end_session": sessions[-1].isoformat(),
        "sessions": len(sessions),
        "future_market_data_used": any(bool(item.get("future_data_used")) for item in audits),
        "model_contract": frozen_contract,
        "leaderboard": leaderboard,
    }


def _aggregate_folds(folds: list[dict[str, Any]], initial_nav: float) -> list[dict[str, Any]]:
    ids = sorted(
        {
            str(row["strategy"])
            for fold in folds
            for row in fold["tournament"]["leaderboard"]["rows"]
        }
    )
    out: list[dict[str, Any]] = []
    for strategy in ids:
        current_nav = initial_nav
        curve: list[dict[str, Any]] = []
        trades = 0
        normalized_costs = 0.0
        positive_folds = 0
        fold_returns: list[float] = []
        for fold in folds:
            row = next(
                item
                for item in fold["tournament"]["leaderboard"]["rows"]
                if item["strategy"] == strategy
            )
            start_nav = current_nav
            fold_initial = float(fold["tournament"]["leaderboard"]["initial_nav_usd"])
            for point in row.get("equity_curve", []):
                scaled = start_nav * (float(point["nav"]) / fold_initial)
                if curve and curve[-1]["session"] == point["session"]:
                    curve[-1] = {"session": point["session"], "nav": scaled}
                else:
                    curve.append({"session": point["session"], "nav": scaled})
            ret = float(row["return"])
            fold_returns.append(ret)
            positive_folds += int(ret > 0)
            current_nav = start_nav * (1.0 + ret)
            trades += int(row.get("trades", 0))
            normalized_costs += float(row.get("costs_paid", 0.0))
        out.append(
            {
                "strategy": strategy,
                "nav": current_nav,
                "return": current_nav / initial_nav - 1.0,
                "sharpe": _sharpe(curve),
                "max_drawdown": _max_drawdown(curve),
                "trades": trades,
                "costs_paid_per_10k_fold_sum": normalized_costs,
                "positive_folds": positive_folds,
                "folds": len(folds),
                "mean_fold_return": _mean(fold_returns),
                "equity_curve": curve,
            }
        )
    out.sort(key=lambda row: float(row["return"]), reverse=True)
    spy = next((row for row in out if row["strategy"] == "benchmark_spy"), None)
    for rank, row in enumerate(out, start=1):
        row["rank"] = rank
        row["vs_spy"] = (
            float(row["return"]) - float(spy["return"]) if spy is not None else None
        )
    return out


def run_walk_forward_oos(
    *,
    dataset_path: Path,
    market_db_path: Path,
    output_dir: Path,
    start: date,
    end: date,
    fold_sessions: int = 21,
    universe_path: Path = Path("config/universe.yaml"),
    league_config_path: Path = Path("config/strategy_league.json"),
    strategies_config_path: Path = Path("config/strategies.yaml"),
    freeze_path: Path = Path("config/frozen/capital_challenger_v1.json"),
) -> dict[str, Any]:
    freeze = verify_challenger_freeze(league_config_path, freeze_path)
    full_payload = json.loads(dataset_path.read_text(encoding="utf-8"))
    if not isinstance(full_payload, dict) or not isinstance(full_payload.get("rows"), list):
        raise ValueError("walk-forward requires modelable dataset object with rows")

    symbols = parse_universe_yaml(universe_path)
    daily_rows = load_daily_bars(market_db_path, sorted(set([*symbols, "SPY"])))
    daily_by_symbol = group_by_symbol(daily_rows)
    sessions = _session_dates(daily_by_symbol, start, end)
    chunks = _fold_sessions(sessions, fold_sessions)
    output_dir.mkdir(parents=True, exist_ok=True)

    fold_results: list[dict[str, Any]] = []
    for index, fold in enumerate(chunks, start=1):
        fold_start = fold[0]
        fold_end = fold[-1]
        fold_dir = output_dir / "folds" / f"{index:02d}_{fold_start}_{fold_end}"
        fold_dir.mkdir(parents=True, exist_ok=True)
        training_payload = build_pre_holdout_training_payload(
            full_payload,
            holdout_start=fold_start,
        )
        cutoff = _training_cutoff_audit(training_payload, fold_start)
        training_path = fold_dir / "training_dataset.json"
        training_path.write_text(
            json.dumps(training_payload, ensure_ascii=False),
            encoding="utf-8",
        )
        training_root = fold_dir / "training"
        trained = train_evaluation_suite(
            training_path,
            training_root,
            version=f"walk-forward-{fold_start:%Y%m%d}",
        )
        tournament = _run_fold_tournament(
            sessions=fold,
            daily_by_symbol=daily_by_symbol,
            output_dir=fold_dir,
            universe_path=universe_path,
            model_registry_dir=Path(trained["models_dir"]),
            weekly_model_path=Path(trained["weekly_path"]),
            league_config_path=league_config_path,
            strategies_config_path=strategies_config_path,
        )
        if tournament["future_market_data_used"] is not False:
            raise RuntimeError("walk-forward fold reported future market data")
        fold_result = {
            "fold": index,
            "training_cutoff": cutoff,
            "tournament": tournament,
        }
        (fold_dir / "fold_report.json").write_text(
            json.dumps(fold_result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        fold_results.append(fold_result)

    initial_nav = float(_load_json(league_config_path).get("initial_nav_usd", 10000.0))
    rows = _aggregate_folds(fold_results, initial_nav)
    challenger = next((row for row in rows if row["strategy"] == "capital_allocation_challenger"), None)
    result = {
        "schema_version": 1,
        "status": "MODEL_OOS_OK",
        "evidence_type": "historical_walk_forward_model_oos_fixed_strategy",
        "prospective_evidence": False,
        "historical_model_out_of_sample": True,
        "strategy_configuration_selected_retrospectively": True,
        "future_market_data_used": False,
        "model_training_future_data_used": False,
        "freeze": freeze,
        "start_session": sessions[0].isoformat(),
        "end_session": sessions[-1].isoformat(),
        "sessions": len(sessions),
        "fold_sessions_target": fold_sessions,
        "folds": len(fold_results),
        "initial_nav_usd": initial_nav,
        "methodology": {
            "training_rule": "for each fold: observation < fold_start AND m3 target_end < fold_start",
            "scoring_rule": "models are trained once before each fold and remain frozen inside that fold",
            "execution_rule": "CLOSE signal, next-open execution, same Strategy League costs/stops/holding rules",
            "market_point_in_time": "completed daily bar is used only at that historical session CLOSE",
            "important_limitation": "strategy/config was selected before this replay, so this is model-OOS evidence, not untouched strategy-research OOS",
        },
        "rows": rows,
        "challenger": challenger,
        "fold_reports": [
            {
                "fold": fold["fold"],
                "training_cutoff": fold["training_cutoff"],
                "start_session": fold["tournament"]["start_session"],
                "end_session": fold["tournament"]["end_session"],
                "sessions": fold["tournament"]["sessions"],
                "rows": fold["tournament"]["leaderboard"]["rows"],
            }
            for fold in fold_results
        ],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    output_path = output_dir / "walk_forward_oos.json"
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Run model-OOS walk-forward Strategy League tournament")
    parser.add_argument("--dataset", default="data/training/modelable_dataset.json")
    parser.add_argument("--market-db", default="data/market/market_data.sqlite")
    parser.add_argument("--output", default="data/replay/walk_forward_oos")
    parser.add_argument("--start", default="2026-03-02")
    parser.add_argument("--end", default="2026-09-04")
    parser.add_argument("--fold-sessions", type=int, default=21)
    parser.add_argument("--universe", default="config/universe.yaml")
    parser.add_argument("--league-config", default="config/strategy_league.json")
    parser.add_argument("--strategies-config", default="config/strategies.yaml")
    parser.add_argument("--freeze", default="config/frozen/capital_challenger_v1.json")
    args = parser.parse_args()
    result = run_walk_forward_oos(
        dataset_path=Path(args.dataset),
        market_db_path=Path(args.market_db),
        output_dir=Path(args.output),
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
        fold_sessions=args.fold_sessions,
        universe_path=Path(args.universe),
        league_config_path=Path(args.league_config),
        strategies_config_path=Path(args.strategies_config),
        freeze_path=Path(args.freeze),
    )
    print(json.dumps({
        "status": result["status"],
        "start": result["start_session"],
        "end": result["end_session"],
        "sessions": result["sessions"],
        "folds": result["folds"],
        "challenger": result.get("challenger"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
