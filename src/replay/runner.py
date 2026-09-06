from __future__ import annotations

import argparse
import hashlib
import json
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import fmean
from typing import Any

from floor.pipeline.prediction_runtime import (
    _prediction_payloads,
    _validate_prediction_payload,
    build_prediction_record,
)
from floor.schemas import record_to_dict
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
from strategies.run_strategies import load_simple_yaml
from utils.market_session import ET, checkpoint_times, get_session_info
from floor.calendar import is_market_session

from replay.point_in_time import build_point_in_time_feature_rows, group_by_symbol
from replay.yahoo_source import fetch_replay_market_data

logger = logging.getLogger(__name__)
REQUIRED_SESSIONS = {"d1": 1, "w1": 5, "q1": 10, "m3": 65}


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"expected JSON object at {path}")
    return payload


def _sessions(start: date, end: date) -> list[date]:
    if end < start:
        raise ValueError("end date precedes start date")
    out: list[date] = []
    day = start
    while day <= end:
        if is_market_session(day):
            out.append(day)
        day += timedelta(days=1)
    if not out:
        raise RuntimeError("replay window contains no market sessions")
    return out


def _day(row: dict[str, Any]) -> date:
    return datetime.fromisoformat(str(row["timestamp"]).replace("Z", "+00:00")).astimezone(ET).date()


def _bars_through(
    rows: list[dict[str, Any]],
    session_day: date,
) -> list[dict[str, Any]]:
    return [dict(row) for row in rows if _day(row) <= session_day]


def _bar_for_day(rows: list[dict[str, Any]], session_day: date) -> dict[str, Any] | None:
    matches = [row for row in rows if _day(row) == session_day]
    return dict(matches[-1]) if matches else None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _prediction_rows(
    forecasts: list[dict[str, Any]],
    *,
    as_of: datetime,
    event: str,
    session_day: date,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    batch_id = f"replay:{session_day.isoformat()}:{event}"
    for forecast in forecasts:
        symbol = str(forecast["symbol"]).upper()
        for horizon, payload in _prediction_payloads(forecast, event):
            _validate_prediction_payload(symbol, horizon, payload)
            record = build_prediction_record(
                symbol=symbol,
                as_of=as_of,
                horizon=horizon,
                payload=payload,
                model_version=str(forecast.get("model_version") or "unknown"),
            )
            item = record_to_dict(record)
            item["batch_id"] = batch_id
            item["evidence_type"] = "retrospective_point_in_time_replay"
            rows.append({str(key): value for key, value in item.items()})
    return rows


def _realized_window(
    daily_rows: list[dict[str, Any]],
    as_of_day: date,
    required_sessions: int,
) -> list[dict[str, Any]]:
    future = [dict(row) for row in daily_rows if _day(row) > as_of_day]
    future.sort(key=lambda row: str(row["timestamp"]))
    return future[:required_sessions] if len(future) >= required_sessions else []


def _evaluate_predictions(
    predictions: list[dict[str, Any]],
    daily_by_symbol: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    resolved: list[dict[str, Any]] = []
    pending_by_horizon: dict[str, int] = defaultdict(int)

    for pred in predictions:
        horizon = str(pred["horizon"]).lower()
        required = REQUIRED_SESSIONS[horizon]
        as_of_day = datetime.fromisoformat(
            str(pred["as_of"]).replace("Z", "+00:00")
        ).astimezone(ET).date()
        window = _realized_window(
            daily_by_symbol.get(str(pred["symbol"]).upper(), []),
            as_of_day,
            required,
        )
        if not window:
            pending_by_horizon[horizon] += 1
            continue
        realized_floor = min(float(row["low"]) for row in window)
        realized_ceiling = max(float(row["high"]) for row in window)
        predicted_floor = pred.get("floor_value")
        predicted_ceiling = pred.get("ceiling_value")
        floor_error = (
            abs(float(predicted_floor) - realized_floor)
            if predicted_floor is not None
            else None
        )
        ceiling_error = (
            abs(float(predicted_ceiling) - realized_ceiling)
            if predicted_ceiling is not None
            else None
        )
        coverage = (
            predicted_floor is not None
            and predicted_ceiling is not None
            and float(predicted_floor) <= realized_floor
            and float(predicted_ceiling) >= realized_ceiling
        )
        resolved.append(
            {
                **pred,
                "realized_floor": realized_floor,
                "realized_ceiling": realized_ceiling,
                "abs_error_floor": floor_error,
                "abs_error_ceiling": ceiling_error,
                "range_covered": coverage,
                "window_start": window[0]["timestamp"],
                "window_end": window[-1]["timestamp"],
            }
        )

    def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
        floor_errors = [
            float(row["abs_error_floor"])
            for row in rows
            if row.get("abs_error_floor") is not None
        ]
        ceiling_errors = [
            float(row["abs_error_ceiling"])
            for row in rows
            if row.get("abs_error_ceiling") is not None
        ]
        coverages = [1.0 if row.get("range_covered") else 0.0 for row in rows]
        return {
            "resolved": len(rows),
            "mean_abs_error_floor": round(fmean(floor_errors), 6)
            if floor_errors
            else None,
            "mean_abs_error_ceiling": round(fmean(ceiling_errors), 6)
            if ceiling_errors
            else None,
            "range_coverage_rate": round(fmean(coverages), 6)
            if coverages
            else None,
        }

    by_horizon: dict[str, Any] = {}
    by_event: dict[str, Any] = {}
    for horizon in REQUIRED_SESSIONS:
        rows = [row for row in resolved if row["horizon"] == horizon]
        by_horizon[horizon] = {
            **summarize(rows),
            "pending": int(pending_by_horizon.get(horizon, 0)),
        }
    for event in ["OPEN", "OPEN_PLUS_2H", "OPEN_PLUS_4H", "OPEN_PLUS_6H", "CLOSE"]:
        rows = [row for row in resolved if row["event_type"] == event]
        by_event[event] = summarize(rows)

    return {
        "resolved_rows": resolved,
        "summary": {
            "total_predictions": len(predictions),
            "resolved_predictions": len(resolved),
            "pending_predictions": len(predictions) - len(resolved),
            "by_horizon": by_horizon,
            "by_event": by_event,
        },
    }


def _enrich_league_row(
    feature: dict[str, Any],
    forecast: dict[str, Any],
) -> dict[str, Any]:
    row = dict(feature)
    for horizon in ("d1", "w1", "q1"):
        row[f"floor_{horizon}"] = forecast.get(f"floor_{horizon}")
        row[f"ceiling_{horizon}"] = forecast.get(f"ceiling_{horizon}")
    row["floor_time_bucket_d1"] = forecast.get("floor_time_bucket_d1")
    row["ceiling_time_bucket_d1"] = forecast.get("ceiling_time_bucket_d1")
    row["confidence_score"] = forecast.get("confidence_score")
    for field in (
        "floor_m3",
        "floor_week_m3",
        "floor_week_m3_confidence",
        "floor_week_m3_top3",
        "m3_status",
        "m3_block_reason",
    ):
        row[field] = forecast.get(field)

    floor_d1 = float(row.get("floor_d1") or 0.0)
    ceiling_d1 = float(row.get("ceiling_d1") or 0.0)
    close = float(row.get("close") or 0.0)
    if floor_d1 > 0 and ceiling_d1 > floor_d1 and close > 0:
        row["expected_range_d1"] = ceiling_d1 - floor_d1
        downside = max(close - floor_d1, 1e-9)
        row["reward_risk_ratio"] = max(0.0, ceiling_d1 - close) / downside
    else:
        row["expected_range_d1"] = 0.0
        row["reward_risk_ratio"] = 0.0
    for horizon in ("d1", "w1", "q1", "m3"):
        row[f"expected_return_{horizon}"] = None
    return row


def _run_league_replay(
    *,
    output_dir: Path,
    sessions: list[date],
    symbols: list[str],
    daily_by_symbol: dict[str, list[dict[str, Any]]],
    close_forecasts: dict[str, list[dict[str, Any]]],
    league_config_path: Path,
    strategies_config_path: Path,
    weekly_model_path: Path,
) -> dict[str, Any]:
    league_cfg = _load_json(league_config_path)
    league_cfg["league_id"] = (
        f"strategy_league_replay_{sessions[0].strftime('%Y%m%d')}_"
        f"{sessions[-1].strftime('%Y%m%d')}"
    )
    strategies_cfg = load_simple_yaml(strategies_config_path)
    weekly_cfg = strategies_cfg["strategies"]["weekly_opportunity_ridge"]
    max_holding_sessions = int(
        weekly_cfg.get("exits", {}).get("temporal_exit_business_days", 10) or 10
    )
    league_cfg["strategy_max_holding_sessions"] = {
        "weekly_opportunity_ridge": max_holding_sessions
    }
    weekly_artifact = _load_json(weekly_model_path)
    frozen_contract = {
        "league_config_sha256": sha256_file(league_config_path),
        "strategies_config_sha256": sha256_file(strategies_config_path),
        "weekly_model_sha256": sha256_file(weekly_model_path),
    }
    replay_state_dir = output_dir / "strategy_league" / "runs" / league_cfg["league_id"]
    state: dict[str, Any] | None = None
    spy_rows = daily_by_symbol["SPY"]

    for session_day in sessions:
        session = session_day.isoformat()
        forecasts = {
            str(row["symbol"]).upper(): row
            for row in close_forecasts.get(session, [])
        }
        bars = {
            symbol: bar
            for symbol in [*symbols, "SPY"]
            if (bar := _bar_for_day(daily_by_symbol.get(symbol, []), session_day))
            is not None
        }
        if len(bars) != len(symbols) + 1:
            missing = sorted(set([*symbols, "SPY"]) - set(bars))
            raise RuntimeError(f"league replay missing daily bars session={session}: {missing}")

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
                    f"league replay incomplete inputs session={session} symbol={symbol}"
                )
            feature_rows.append(_enrich_league_row(feature, forecast))

        frequency = max(1, int(league_cfg.get("weekly_review_frequency_sessions", 5)))
        include_weekly = state is None or int(state.get("session_count", 0)) % frequency == 0
        next_targets = _strategy_targets(
            feature_rows,
            strategies_cfg,
            weekly_artifact,
            include_weekly=include_weekly,
        )
        if state is None:
            initial_targets = {
                **next_targets,
                **_benchmark_targets(symbols, "SPY"),
            }
            state = initialize_league(
                replay_state_dir,
                league_cfg,
                session,
                frozen_contract,
                initial_targets,
            )
        else:
            state = advance_league(
                replay_state_dir,
                state,
                league_cfg,
                session,
                bars,
                frozen_contract,
                next_targets,
            )

    assert state is not None
    leaderboard = write_leaderboard(replay_state_dir, state, league_cfg)
    target = output_dir / "strategy_league" / "leaderboard.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(leaderboard, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return leaderboard


def _render_report(
    *,
    sessions: list[date],
    market_summary: dict[str, Any],
    metrics: dict[str, Any],
    leaderboard: dict[str, Any],
    blocked: list[dict[str, Any]],
) -> str:
    by_horizon = metrics["by_horizon"]
    rows = {row["strategy"]: row for row in leaderboard.get("rows", [])}
    lines = [
        "# Floor retrospective point-in-time replay",
        "",
        "> Diagnostic retrospective evidence only. This replay is isolated from "
        "Strategy League v2 and is not prospective evidence.",
        "",
        f"- Window: {sessions[0].isoformat()} to {sessions[-1].isoformat()} "
        f"({len(sessions)} market sessions)",
        f"- Universe: {market_summary['symbols'] - 1} equities + SPY",
        f"- Intraday source: {market_summary['intraday_source']}",
        f"- Blocked forecast rows: {len(blocked)}",
        "",
        "## Forecast evidence available as of replay execution",
        "",
        "| Horizon | Resolved | Pending | Floor MAE | Ceiling MAE | Coverage |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for horizon in ("d1", "w1", "q1", "m3"):
        item = by_horizon[horizon]
        coverage = item["range_coverage_rate"]
        coverage_text = "n/a" if coverage is None else f"{100*coverage:.2f}%"
        floor_text = (
            "n/a"
            if item["mean_abs_error_floor"] is None
            else f"{item['mean_abs_error_floor']:.4f}"
        )
        ceiling_text = (
            "n/a"
            if item["mean_abs_error_ceiling"] is None
            else f"{item['mean_abs_error_ceiling']:.4f}"
        )
        lines.append(
            f"| {horizon} | {item['resolved']} | {item['pending']} | "
            f"{floor_text} | {ceiling_text} | {coverage_text} |"
        )

    lines.extend(["", "## Strategy League replay", ""])
    for strategy in (
        "weekly_opportunity_ridge",
        "breakout_protected_by_floor",
        "benchmark_spy",
        "benchmark_equal_weight",
    ):
        row = rows.get(strategy)
        if not row:
            continue
        lines.append(
            f"- **{strategy}**: return={100*float(row['return']):.3f}%, "
            f"max_drawdown={100*float(row['max_drawdown']):.3f}%, "
            f"trades={int(row['trades'])}, costs=${float(row['costs_paid']):.2f}"
        )
    lines.extend(
        [
            "",
            "## Interpretation rules",
            "",
            "- This replay never writes to runtime-state, Pages, production predictions, or Strategy League v2.",
            "- Daily history is cut before each replay session; the current session is rebuilt from 5-minute bars only up to the checkpoint.",
            "- OPEN uses only the opening print; no completed future intraday bar is admitted.",
            "- q1 and m3 evidence remains pending when the required future sessions have not occurred yet.",
            "- Model/config hashes are recorded in replay_manifest.json.",
            "",
        ]
    )
    return "\n".join(lines)


def run_replay(
    *,
    start: date,
    end: date,
    output_dir: Path,
    universe_path: Path,
    model_registry_dir: Path,
    league_config_path: Path,
    strategies_config_path: Path,
    weekly_model_path: Path,
) -> dict[str, Any]:
    sessions = _sessions(start, end)
    symbols = parse_universe_yaml(universe_path)
    daily_rows, intraday_rows, market_summary = fetch_replay_market_data(symbols)
    daily_by_symbol = group_by_symbol(daily_rows)
    intraday_by_symbol = group_by_symbol(intraday_rows)

    output_dir.mkdir(parents=True, exist_ok=True)
    all_predictions: list[dict[str, Any]] = []
    all_audits: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    close_forecasts: dict[str, list[dict[str, Any]]] = {}

    for session_day in sessions:
        info = get_session_info(
            datetime.combine(session_day, datetime.min.time(), tzinfo=ET)
        )
        events = list(checkpoint_times(info))
        for event in events:
            feature_rows, audit = build_point_in_time_feature_rows(
                daily_by_symbol=daily_by_symbol,
                intraday_by_symbol=intraday_by_symbol,
                symbols=symbols,
                benchmark_symbol="SPY",
                session_day=session_day,
                event=event,
            )
            as_of = datetime.fromisoformat(audit["checkpoint"])
            generated = run_forecast_pipeline(
                market_rows=feature_rows,
                ai_by_symbol={},
                session=event,
                as_of=as_of,
                model_registry_dir=model_registry_dir,
            )
            forecasts = list(generated["dataset_forecasts"])
            blocked_rows = list(generated["blocked_list"])
            if blocked_rows:
                blocked.extend(
                    [
                        {
                            **row,
                            "session": session_day.isoformat(),
                            "event": event,
                        }
                        for row in blocked_rows
                    ]
                )
            if len(forecasts) != len(symbols):
                raise RuntimeError(
                    f"incomplete replay forecast batch session={session_day} "
                    f"event={event} forecasts={len(forecasts)} expected={len(symbols)}"
                )
            all_predictions.extend(
                _prediction_rows(
                    forecasts,
                    as_of=as_of,
                    event=event,
                    session_day=session_day,
                )
            )
            all_audits.append(audit)
            if event == "CLOSE":
                close_forecasts[session_day.isoformat()] = forecasts

    if blocked:
        raise RuntimeError(f"replay produced blocked forecasts: {blocked[:10]}")

    evaluation = _evaluate_predictions(all_predictions, daily_by_symbol)
    leaderboard = _run_league_replay(
        output_dir=output_dir,
        sessions=sessions,
        symbols=symbols,
        daily_by_symbol=daily_by_symbol,
        close_forecasts=close_forecasts,
        league_config_path=league_config_path,
        strategies_config_path=strategies_config_path,
        weekly_model_path=weekly_model_path,
    )

    predictions_path = output_dir / "predictions.jsonl"
    with predictions_path.open("w", encoding="utf-8") as handle:
        for row in all_predictions:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    reconciled_path = output_dir / "reconciled_predictions.jsonl"
    with reconciled_path.open("w", encoding="utf-8") as handle:
        for row in evaluation["resolved_rows"]:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    audits_path = output_dir / "point_in_time_audit.jsonl"
    with audits_path.open("w", encoding="utf-8") as handle:
        for row in all_audits:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    metrics = evaluation["summary"]
    (output_dir / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    model_hashes = {
        path.name: _sha256(path)
        for path in sorted(model_registry_dir.glob("*_champion.json"))
        if path.is_file()
    }
    manifest = {
        "schema_version": 1,
        "evidence_type": "retrospective_point_in_time_replay",
        "prospective_evidence": False,
        "start_session": sessions[0].isoformat(),
        "end_session": sessions[-1].isoformat(),
        "market_sessions": [day.isoformat() for day in sessions],
        "events": ["OPEN", "OPEN_PLUS_2H", "OPEN_PLUS_4H", "OPEN_PLUS_6H", "CLOSE"],
        "universe_size": len(symbols),
        "benchmark": "SPY",
        "market_source": market_summary,
        "model_hashes": model_hashes,
        "league_config_sha256": _sha256(league_config_path),
        "strategies_config_sha256": _sha256(strategies_config_path),
        "weekly_model_sha256": _sha256(weekly_model_path),
        "isolation": {
            "runtime_state_writes": False,
            "pages_writes": False,
            "production_prediction_writes": False,
            "strategy_league_v2_writes": False,
        },
        "anti_leakage": {
            "daily_rows_strictly_before_session_for_intraday_scoring": True,
            "current_session_rebuilt_from_5m_bars": True,
            "intraday_bar_start_must_be_before_checkpoint": True,
            "open_uses_opening_print_only": True,
        },
        "prediction_count": len(all_predictions),
        "blocked_count": len(blocked),
    }
    (output_dir / "replay_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report = _render_report(
        sessions=sessions,
        market_summary=market_summary,
        metrics=metrics,
        leaderboard=leaderboard,
        blocked=blocked,
    )
    (output_dir / "REPLAY_REPORT.md").write_text(report, encoding="utf-8")
    return {
        "output_dir": str(output_dir),
        "sessions": len(sessions),
        "predictions": len(all_predictions),
        "resolved": metrics["resolved_predictions"],
        "leaderboard": leaderboard,
    }


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    parser = argparse.ArgumentParser(
        description="Run an isolated point-in-time retrospective replay"
    )
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
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

    result = run_replay(
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
        output_dir=Path(args.output),
        universe_path=Path(args.universe),
        model_registry_dir=Path(args.model_registry),
        league_config_path=Path(args.league_config),
        strategies_config_path=Path(args.strategies_config),
        weekly_model_path=Path(args.weekly_model),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
