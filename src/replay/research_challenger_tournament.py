from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any

from contracts.trading import round_trip_cost_bps_from_contract, shadow_execution_contract
from floor.universe import parse_universe_yaml
from forecasting.run_forecast import run_forecast_pipeline
from league.engine import advance_league, initialize_league, sha256_file, write_leaderboard
from league.market_features import _feature_row
from league.run_eod import (
    _benchmark_targets,
    _decision_targets,
    _holding_sessions,
    _strategy_targets,
)
from models.train_weekly_opportunity import predict_weekly_opportunity
from replay.capital_tournament import _select_complete_session_run
from replay.historical_close import build_historical_close_feature_rows
from replay.point_in_time import group_by_symbol
from replay.runner import _bar_for_day, _bars_through, _enrich_league_row, _load_json, _sessions
from replay.yahoo_source import fetch_replay_daily_market_data
from strategies.breakout_protected_by_floor import generate_breakout_floor_orders
from strategies.mean_reversion_floor_w1 import generate_mean_reversion_orders
from strategies.relative_strength_rotation import build_relative_strength_rotation
from strategies.run_strategies import load_simple_yaml
from strategies.volatility_regime_switch import route_volatility_regime_decisions
from strategies.weekly_opportunity_ridge import generate_weekly_opportunity_orders


RESEARCH_MEMBERS = (
    "volatility_regime_switch",
    "relative_strength_rotation",
)


def _runtime_config(
    league_cfg: dict[str, Any],
    strategies_cfg: dict[str, Any],
    sessions: list[date],
    volatility_cfg: dict[str, Any],
    rotation_cfg: dict[str, Any],
) -> dict[str, Any]:
    base_members = [dict(spec) for spec in league_cfg.get("members", [])]
    existing_ids = {str(spec.get("id") or "") for spec in base_members}
    for member_id in RESEARCH_MEMBERS:
        if member_id in existing_ids:
            continue
        base_members.append(
            {
                "id": member_id,
                "type": "strategy",
                "required": False,
                "evidence_role": "diagnostic_only",
                "promotion_eligible": False,
                "evaluation_variant": "long_only_projection",
                "canonical_variant": "research_meta_strategy",
                "league_evidence_can_promote_canonical_variant": False,
            }
        )

    strategy_configs = strategies_cfg["strategies"]
    challenger_cfg = dict(league_cfg.get("capital_allocation_challenger", {}))
    return {
        **league_cfg,
        "league_id": (
            f"research_challenger_closeout_{sessions[0]:%Y%m%d}_{sessions[-1]:%Y%m%d}"
        ),
        "members": base_members,
        "strategy_max_holding_sessions": {
            "weekly_opportunity_ridge": _holding_sessions(
                strategy_configs["weekly_opportunity_ridge"], 10
            ),
            "mean_reversion_floor_w1": _holding_sessions(
                strategy_configs["mean_reversion_floor_w1"], 5
            ),
            "cross_horizon_asymmetry": _holding_sessions(
                strategy_configs["cross_horizon_asymmetry"], 10
            ),
            "capital_allocation_challenger": int(
                challenger_cfg.get("max_holding_sessions", 10) or 10
            ),
            "volatility_regime_switch": max(
                1, int(volatility_cfg.get("max_holding_sessions", 5) or 5)
            ),
            "relative_strength_rotation": max(
                1, int(rotation_cfg.get("max_holding_sessions", 10) or 10)
            ),
        },
    }


def _source_decisions(
    rows: list[dict[str, Any]],
    strategies_cfg: dict[str, Any],
    weekly_artifact: dict[str, Any],
    *,
    held_weekly_symbols: set[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, list[Any]]]:
    scored = [dict(row) for row in rows]
    params = weekly_artifact.get("params", {})
    for row in scored:
        row["weekly_opportunity_score"] = predict_weekly_opportunity(row, params)

    strategy_configs = strategies_cfg["strategies"]
    weekly = generate_weekly_opportunity_orders(
        scored,
        strategies_cfg,
        strategy_configs["weekly_opportunity_ridge"],
        "CLOSE",
        held_symbols=held_weekly_symbols or set(),
    )
    breakout = generate_breakout_floor_orders(
        scored,
        strategies_cfg,
        strategy_configs["breakout_protected_by_floor"],
        "CLOSE",
    )
    mean_reversion = generate_mean_reversion_orders(
        scored,
        strategies_cfg,
        strategy_configs["mean_reversion_floor_w1"],
        "CLOSE",
    )
    return scored, {
        "weekly_opportunity_ridge": weekly,
        "breakout_protected_by_floor": breakout,
        "mean_reversion_floor_w1": mean_reversion,
    }


def _research_targets(
    *,
    rows: list[dict[str, Any]],
    strategies_cfg: dict[str, Any],
    weekly_artifact: dict[str, Any],
    volatility_cfg: dict[str, Any],
    rotation_cfg: dict[str, Any],
    current_positions: dict[str, set[str]],
    include_volatility: bool,
    include_rotation: bool,
) -> tuple[dict[str, dict[str, dict]], dict[str, Counter[str]], dict[str, int]]:
    scored, sources = _source_decisions(
        rows,
        strategies_cfg,
        weekly_artifact,
        held_weekly_symbols=current_positions.get("weekly_opportunity_ridge", set()),
    )
    rows_by_symbol = {str(row.get("symbol")): row for row in scored}
    targets: dict[str, dict[str, dict]] = {}
    action_counts: dict[str, Counter[str]] = {
        "volatility_regime_switch": Counter(),
        "relative_strength_rotation": Counter(),
    }
    feature_coverage = {
        "volatility_regime_switch": sum(
            1
            for row in scored
            if row.get("vol_regime") not in (None, "")
            or row.get("vol_regime_score") is not None
        ),
        "relative_strength_rotation": sum(
            1
            for row in scored
            if sum(
                row.get(field) is not None
                for field in ("rel_strength_4w", "rel_strength_8w", "rel_strength_13w")
            )
            >= 2
        ),
    }

    if include_volatility:
        decisions = route_volatility_regime_decisions(
            sources,
            rows_by_symbol,
            volatility_cfg,
        )
        action_counts["volatility_regime_switch"].update(
            str(item.side).upper() for item in decisions
        )
        targets["volatility_regime_switch"] = _decision_targets(
            decisions,
            rows_by_symbol,
            strategies_cfg,
        )

    if include_rotation:
        decisions = build_relative_strength_rotation(
            sources,
            rows_by_symbol,
            rotation_cfg,
            held_symbols=current_positions.get("relative_strength_rotation", set()),
        )
        action_counts["relative_strength_rotation"].update(
            str(item.side).upper() for item in decisions
        )
        targets["relative_strength_rotation"] = _decision_targets(
            decisions,
            rows_by_symbol,
            strategies_cfg,
        )

    return targets, action_counts, feature_coverage


def _evidence_readiness(
    leaderboard: dict[str, Any],
    action_totals: dict[str, Counter[str]],
    feature_coverage: dict[str, int],
    total_feature_rows: int,
) -> dict[str, Any]:
    rows = {
        str(row.get("strategy")): row
        for row in leaderboard.get("rows", [])
        if isinstance(row, dict)
    }
    coverage_denominator = max(total_feature_rows, 1)
    evaluated: dict[str, Any] = {}
    for member_id in RESEARCH_MEMBERS:
        row = rows.get(member_id)
        evaluated[member_id] = {
            "status": "EVALUATED_RETROSPECTIVE_PIT" if row is not None else "NOT_EVALUATED",
            "evaluated": row is not None,
            "prospective_evidence": False,
            "historical_model_out_of_sample": False,
            "feature_coverage_ratio": feature_coverage.get(member_id, 0)
            / coverage_denominator,
            "action_counts": dict(action_totals.get(member_id, Counter())),
            "promotion_eligible": False,
        }

    evaluated["floor_ceiling_reclaim"] = {
        "status": "INSUFFICIENT_EVIDENCE",
        "evaluated": False,
        "prospective_evidence": False,
        "historical_model_out_of_sample": False,
        "missing": [
            "point-in-time intraday reclaim path",
            "floor_reclaim_alpha_pct / ceiling_rejection_alpha_pct history",
        ],
        "reason": (
            "The strategy contract requires explicit point-in-time directional alpha. "
            "A retrospective event touch/reclaim is not converted into alpha."
        ),
        "promotion_eligible": False,
    }
    evaluated["gap_to_floor_ceiling"] = {
        "status": "INSUFFICIENT_EVIDENCE",
        "evaluated": False,
        "prospective_evidence": False,
        "historical_model_out_of_sample": False,
        "missing": [
            "OPEN-aligned prior forecast context",
            "gap_floor_alpha_pct / gap_ceiling_alpha_pct history",
        ],
        "reason": (
            "The strategy contract requires OPEN-time geometry and explicit directional "
            "gap alpha. Daily CLOSE replay is not relabeled after the fact."
        ),
        "promotion_eligible": False,
    }
    return evaluated


def _render_closeout(payload: dict[str, Any]) -> str:
    rows = {
        str(row.get("strategy")): row
        for row in payload.get("leaderboard", {}).get("rows", [])
        if isinstance(row, dict)
    }
    lines = [
        "# Research strategy closeout",
        "",
        "> Retrospective point-in-time diagnostic only. This artifact is not prospective "
        "evidence and is not pure historical model OOS proof.",
        "",
        f"- Window: {payload['start_session']} to {payload['end_session']} "
        f"({payload['sessions']} sessions)",
        f"- Round-trip execution cost contract: {payload['round_trip_cost_bps']:.0f} bps",
        "- Portfolio continuity: yes",
        "- Strategy League v9 evidence mutated: no",
        "",
        "## Evaluated research meta-strategies",
        "",
        "| Strategy | Return | Sharpe | Max DD | Trades | Costs | Turnover |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for member_id in RESEARCH_MEMBERS:
        row = rows.get(member_id)
        if not row:
            continue
        sharpe = row.get("sharpe")
        sharpe_text = "n/a" if sharpe is None else f"{float(sharpe):.2f}"
        lines.append(
            f"| {member_id} | {100*float(row.get('return', 0.0)):.2f}% | "
            f"{sharpe_text} | {100*float(row.get('max_drawdown', 0.0)):.2f}% | "
            f"{int(row.get('trades', 0))} | USD {float(row.get('costs_paid', 0.0)):.2f} | "
            f"{float(row.get('turnover_review_window') or row.get('turnover') or 0.0):.2f} |"
        )

    lines.extend(
        [
            "",
            "## Evidence readiness",
            "",
            "| Strategy | Status | Can promote? |",
            "| --- | --- | --- |",
        ]
    )
    for strategy_id, item in payload["evidence_readiness"].items():
        lines.append(
            f"| {strategy_id} | {item['status']} | no |"
        )

    lines.extend(
        [
            "",
            "## Closeout",
            "",
            "- Volatility Regime Switch and Relative Strength Rotation are now measurable "
            "as isolated retrospective meta-strategies.",
            "- Floor/Ceiling Reclaim and Gap-to-Floor/Ceiling remain research-only until "
            "their explicit point-in-time directional alpha fields exist historically or "
            "accumulate prospectively.",
            "- No research result in this report changes Strategy League v9 or enables live execution.",
            "",
        ]
    )
    return "\n".join(lines)


def run_research_challenger_tournament(
    *,
    start: date,
    end: date,
    output_dir: Path,
    universe_path: Path = Path("config/universe.yaml"),
    model_registry_dir: Path = Path("data/training/models"),
    league_config_path: Path = Path("config/strategy_league.json"),
    strategies_config_path: Path = Path("config/strategies.yaml"),
    weekly_model_path: Path | None = None,
    volatility_config_path: Path = Path("config/research/volatility_regime_switch.yaml"),
    rotation_config_path: Path = Path("config/research/relative_strength_rotation.yaml"),
) -> dict[str, Any]:
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

    base_league_cfg = _load_json(league_config_path)
    if weekly_model_path is None:
        configured = str(base_league_cfg.get("weekly_model_path") or "").strip()
        if not configured:
            raise ValueError("weekly_model_path missing from Strategy League config")
        weekly_model_path = Path(configured)

    strategies_cfg = load_simple_yaml(strategies_config_path)
    strategies_cfg["costs"] = shadow_execution_contract()
    volatility_cfg = load_simple_yaml(volatility_config_path).get("strategy", {})
    rotation_cfg = load_simple_yaml(rotation_config_path).get("strategy", {})
    weekly_artifact = _load_json(weekly_model_path)
    league_cfg = _runtime_config(
        base_league_cfg,
        strategies_cfg,
        sessions,
        volatility_cfg,
        rotation_cfg,
    )

    weekly_holding = _holding_sessions(
        strategies_cfg["strategies"]["weekly_opportunity_ridge"], 10
    )
    mean_holding = _holding_sessions(
        strategies_cfg["strategies"]["mean_reversion_floor_w1"], 5
    )
    cross_holding = _holding_sessions(
        strategies_cfg["strategies"]["cross_horizon_asymmetry"], 10
    )
    challenger_cfg = dict(base_league_cfg.get("capital_allocation_challenger", {}))
    challenger_frequency = max(
        1, int(challenger_cfg.get("review_frequency_sessions", weekly_holding) or weekly_holding)
    )
    weekly_frequency = max(
        weekly_holding,
        int(base_league_cfg.get("weekly_review_frequency_sessions", weekly_holding)),
    )
    volatility_frequency = max(
        1, int(volatility_cfg.get("review_frequency_sessions", 1) or 1)
    )
    rotation_frequency = max(
        1, int(rotation_cfg.get("review_frequency_sessions", 5) or 5)
    )

    frozen_contract = {
        "league_config_sha256": sha256_file(league_config_path),
        "strategies_config_sha256": sha256_file(strategies_config_path),
        "weekly_model_sha256": sha256_file(weekly_model_path),
        "volatility_config_sha256": sha256_file(volatility_config_path),
        "rotation_config_sha256": sha256_file(rotation_config_path),
        **{
            f"{task}_champion_sha256": sha256_file(model_registry_dir / f"{task}_champion.json")
            for task in ("d1", "w1", "q1", "value", "timing")
        },
    }

    run_dir = output_dir / "runs" / str(league_cfg["league_id"])
    state: dict[str, Any] | None = None
    spy_rows = daily_by_symbol["SPY"]
    audits: list[dict[str, Any]] = []
    action_totals: dict[str, Counter[str]] = {
        member_id: Counter()
        for member_id in RESEARCH_MEMBERS
    }
    feature_coverage = {
        member_id: 0
        for member_id in RESEARCH_MEMBERS
    }
    total_feature_rows = 0

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
            raise RuntimeError(f"future data detected session={session}")

        generated = run_forecast_pipeline(
            market_rows=pit_rows,
            ai_by_symbol={},
            session="CLOSE",
            as_of=datetime.fromisoformat(str(audit["checkpoint"])),
            model_registry_dir=model_registry_dir,
        )
        forecasts_list = list(generated["dataset_forecasts"])
        blocked = list(generated["blocked_list"])
        if blocked or len(forecasts_list) != len(symbols):
            raise RuntimeError(
                f"incomplete research forecast session={session} "
                f"blocked={blocked[:3]} rows={len(forecasts_list)} expected={len(symbols)}"
            )
        forecasts = {
            str(row["symbol"]).upper(): row
            for row in forecasts_list
        }

        bars = {
            symbol: bar
            for symbol in [*symbols, "SPY"]
            if (bar := _bar_for_day(daily_by_symbol.get(symbol, []), session_day))
            is not None
        }
        if len(bars) != len(symbols) + 1:
            missing = sorted(set([*symbols, "SPY"]) - set(bars))
            raise RuntimeError(f"missing bars session={session}: {missing}")

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
                raise RuntimeError(f"incomplete row session={session} symbol={symbol}")
            feature_rows.append(_enrich_league_row(feature, forecast))

        count = int(state.get("session_count", 0)) if state is not None else 0
        current_positions = {
            member_id: set(member.get("positions", {}))
            for member_id, member in (state or {}).get("members", {}).items()
            if isinstance(member, dict)
        }
        base_targets = _strategy_targets(
            feature_rows,
            strategies_cfg,
            weekly_artifact,
            include_weekly=state is None or count % weekly_frequency == 0,
            include_mean_reversion=state is None or count % mean_holding == 0,
            include_cross_horizon=state is None or count % cross_holding == 0,
            include_challenger=state is None or count % challenger_frequency == 0,
            challenger_cfg=challenger_cfg,
            current_positions_by_strategy=current_positions,
        )
        research_targets, counts, coverage = _research_targets(
            rows=feature_rows,
            strategies_cfg=strategies_cfg,
            weekly_artifact=weekly_artifact,
            volatility_cfg=volatility_cfg,
            rotation_cfg=rotation_cfg,
            current_positions=current_positions,
            include_volatility=state is None or count % volatility_frequency == 0,
            include_rotation=state is None or count % rotation_frequency == 0,
        )
        total_feature_rows += len(feature_rows)
        for member_id in RESEARCH_MEMBERS:
            action_totals[member_id].update(counts[member_id])
            feature_coverage[member_id] += int(coverage[member_id])

        next_targets = {
            **base_targets,
            **research_targets,
        }
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
        raise RuntimeError("research challenger tournament produced no state")

    leaderboard = write_leaderboard(run_dir, state, league_cfg)
    evidence_readiness = _evidence_readiness(
        leaderboard,
        action_totals,
        feature_coverage,
        total_feature_rows,
    )
    contract = shadow_execution_contract()
    result = {
        "schema_version": 1,
        "status": "RESEARCH_STRATEGY_CLOSEOUT",
        "league_id": str(league_cfg["league_id"]),
        "evidence_type": "retrospective_point_in_time_research_challenger_tournament",
        "prospective_evidence": False,
        "historical_model_out_of_sample": False,
        "portfolio_continuity": True,
        "future_data_used": any(bool(row.get("future_data_used")) for row in audits),
        "mutates_strategy_league_v9": False,
        "live_execution_enabled": False,
        "requested_start_session": requested_sessions[0].isoformat(),
        "requested_end_session": requested_sessions[-1].isoformat(),
        "requested_sessions": len(requested_sessions),
        "start_session": sessions[0].isoformat(),
        "end_session": sessions[-1].isoformat(),
        "sessions": len(sessions),
        "initial_nav_usd": float(league_cfg["initial_nav_usd"]),
        "round_trip_cost_bps": round_trip_cost_bps_from_contract(contract),
        "market_source": {
            **market_summary,
            "session_selection": session_selection,
        },
        "evidence_readiness": evidence_readiness,
        "leaderboard": leaderboard,
    }
    if result["future_data_used"] is not False:
        raise RuntimeError("research closeout refuses future_data_used=true")

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "research_challenger_tournament.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "RESEARCH_STRATEGY_CLOSEOUT.md").write_text(
        _render_closeout(result),
        encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the final retrospective PIT research-strategy closeout tournament"
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

    result = run_research_challenger_tournament(
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
        output_dir=Path(args.output),
        universe_path=Path(args.universe),
        model_registry_dir=Path(args.model_registry),
        league_config_path=Path(args.league_config),
        strategies_config_path=Path(args.strategies_config),
        weekly_model_path=Path(args.weekly_model) if args.weekly_model else None,
    )
    rows = {
        str(row.get("strategy")): row
        for row in result["leaderboard"]["rows"]
    }
    print(
        json.dumps(
            {
                "status": result["status"],
                "sessions": result["sessions"],
                "round_trip_cost_bps": result["round_trip_cost_bps"],
                "research_rows": {
                    member_id: rows.get(member_id)
                    for member_id in RESEARCH_MEMBERS
                },
                "evidence_readiness": result["evidence_readiness"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
