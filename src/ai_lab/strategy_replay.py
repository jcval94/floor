"""Isolated strategy research using FLOOR's existing league ledger and fills."""

from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re

from ai_lab.frozen_models import REQUIRED_INPUTS
from ai_lab.recipes import instant
from ai_lab.store import canonical, digest
from contracts.trading import (
    load_strategy_runtime_config,
    round_trip_cost_bps_from_contract,
)
from forecasting.generate_forecasts import generate_forecasts
from league.engine import (
    initialize_league,
    advance_league,
    build_leaderboard,
    _append_record,
    _trade,
)
from league.market_features import _feature_row
from league.run_eod import _decision_targets, _retain_hold_positions, _benchmark_targets
from strategies.mean_reversion_floor_w1 import generate_mean_reversion_orders
from strategies.breakout_protected_by_floor import generate_breakout_floor_orders

REPO = Path(__file__).resolve().parents[2]
STRATEGIES = {
    "mean_reversion_floor_w1": generate_mean_reversion_orders,
    "breakout_protected_by_floor": generate_breakout_floor_orders,
}
SOURCE_COLUMNS = set(REQUIRED_INPUTS) | {
    "symbol",
    "timestamp",
    "available_at",
    "open",
    "high",
    "low",
    "close",
    "volume",
}


def strategy_catalog():
    cfg = load_strategy_runtime_config()
    return {
        "strategies": [
            {
                "id": s,
                "scope": "retrospective_long_only_replay",
                "promotion_eligible": False,
                "config_sha256": digest((REPO / "config/strategies.yaml").read_bytes()),
            }
            for s in STRATEGIES
        ],
        "round_trip_cost_bps": round_trip_cost_bps_from_contract(cfg["costs"]),
        "observed_at": datetime.now(tz=timezone.utc).isoformat(),
        "cost_config_sha256": digest((REPO / "config/costs.yaml").read_bytes()),
        "risk_config_sha256": digest((REPO / "config/risk.yaml").read_bytes()),
        "benchmarks": ["benchmark_spy", "benchmark_equal_weight"],
    }


def validate_strategy_inputs(rows, manifest, plan):
    if plan.objective != "net_return":
        raise ValueError("STRATEGY_OBJECTIVE_REQUIRED")
    if len(set(plan.strategies)) != len(plan.strategies) or any(
        s not in STRATEGIES for s in plan.strategies
    ):
        raise ValueError("STRATEGY_NOT_REGISTERED")
    if (
        not set(REQUIRED_INPUTS) <= set(manifest.get("materialized_features", []))
        or manifest.get("feature_definition_version")
        != "floor-feature-builder/full-v2-benchmark-joined"
    ):
        raise ValueError("STRATEGY_FULL_FEATURE_MATERIALIZATION_REQUIRED")
    cfg = load_strategy_runtime_config()
    activation = cfg.get("activation", {})
    if activation.get("paper_execution_enabled") or activation.get(
        "live_execution_enabled"
    ):
        raise ValueError("BROKER_FLAGS_MUST_REMAIN_DISABLED")
    league = json.loads((REPO / "config/strategy_league.json").read_text())
    if (
        abs(
            round_trip_cost_bps_from_contract(league["execution"])
            - round_trip_cost_bps_from_contract(cfg["costs"])
        )
        > 1e-9
    ):
        raise ValueError("COST_CONTRACT_DRIFT")
    for field in (
        "commission_bps",
        "broker_commission_bps",
        "platform_fee_bps_per_side",
        "slippage_bps",
        "sell_fee_bps",
    ):
        if (
            field not in league["execution"]
            or field not in cfg["costs"]
            or abs(float(league["execution"][field]) - float(cfg["costs"][field]))
            > 1e-9
        ):
            raise ValueError("COST_CONTRACT_COMPONENT_DRIFT")
    assets = []
    for task in ("d1", "w1", "q1", "value", "timing"):
        path = REPO / "data/training/models" / f"{task}_champion.json"
        artifact = json.loads(path.read_text())
        stamp = re.match(r"^(\d{8}T\d{6}Z)", str(artifact.get("version", "")))
        if not stamp:
            raise ValueError("STRATEGY_MODEL_TIMESTAMP_UNKNOWN")
        assets.append(
            {
                "id": task + "_champion",
                "version": artifact["version"],
                "sha256": digest(path.read_bytes()),
                "artifact_timestamp": datetime.strptime(stamp[1], "%Y%m%dT%H%M%SZ")
                .replace(tzinfo=timezone.utc)
                .isoformat(),
            }
        )
    cutoff = max(instant(a["artifact_timestamp"]) for a in assets)
    dates = sorted(
        {r["timestamp"][:10] for r in rows if instant(r["timestamp"]) > cutoff}
    )
    if not 10 <= len(dates) <= 120:
        raise ValueError("STRATEGY_WINDOW_OUT_OF_POLICY")
    symbols = sorted({r["symbol"] for r in rows})
    if "SPY" not in symbols or not 2 <= len(symbols) <= 3:
        raise ValueError("STRATEGY_BENCHMARK_OR_UNIVERSE_INVALID")
    import exchange_calendars as xc

    cal = xc.get_calendar("XNYS")
    expected = [str(d.date()) for d in cal.sessions_in_range(dates[0], dates[-1])]
    if dates != expected:
        raise ValueError("STRATEGY_MISSING_MARKET_SESSIONS")
    by_session = {d: [] for d in dates}
    for r in rows:
        if r["timestamp"][:10] not in by_session:
            continue
        day = r["timestamp"][:10]
        for f in REQUIRED_INPUTS:
            if (
                r.get(f) is None
                or not math.isfinite(float(r[f]))
                or f not in r.get("feature_available_at", {})
                or instant(r["feature_available_at"][f]) > instant(r["timestamp"])
            ):
                raise ValueError("STRATEGY_FEATURE_AVAILABILITY_INVALID")
        if instant(r["timestamp"]) < cal.session_close(day).to_pydatetime():
            raise ValueError("STRATEGY_UNFINISHED_SIGNAL_BAR")
        for field in ("open", "high", "low", "close"):
            if not math.isfinite(float(r.get(field, 0))) or float(r.get(field, 0)) <= 0:
                raise ValueError("STRATEGY_INVALID_OHLC")
        if (
            not float(r["low"])
            <= min(float(r["open"]), float(r["close"]))
            <= max(float(r["open"]), float(r["close"]))
            <= float(r["high"])
        ):
            raise ValueError("STRATEGY_INVALID_OHLC")
        if (
            not math.isfinite(float(r.get("volume", -1)))
            or float(r.get("volume", -1)) < 0
        ):
            raise ValueError("STRATEGY_INVALID_VOLUME")
        by_session[day].append(r)
    for i, day in enumerate(dates):
        if sorted(r["symbol"] for r in by_session[day]) != symbols:
            raise ValueError("STRATEGY_INCOMPLETE_SESSION_UNIVERSE")
        if i and any(
            instant(r["available_at"]) >= cal.session_open(day).to_pydatetime()
            for r in by_session[dates[i - 1]]
        ):
            raise ValueError("STRATEGY_SIGNAL_NOT_AVAILABLE_BEFORE_FILL")
    return cfg, league, assets, cutoff, dates, symbols


def forecast_inputs(rows, day, symbols):
    """Strip every target/realization before any strategy/forecast call."""
    return [
        {k: v for k, v in r.items() if k in SOURCE_COLUMNS}
        for r in rows
        if r["timestamp"][:10] == day and r["symbol"] in symbols
    ]


def close_targets(day_rows, forecasts, histories, state, cfg, selected):
    by_symbol = {r["symbol"]: r for r in forecasts}
    spy_history = histories["SPY"]
    features = []
    for symbol in sorted(s for s in histories if s != "SPY"):
        feature = _feature_row(symbol, histories[symbol], spy_history)
        if feature is None or symbol not in by_symbol:
            raise ValueError("STRATEGY_FORECAST_OR_HISTORY_MISSING")
        # Native forecasts overwrite all floor/ceiling fields; labels were stripped.
        features.append({**feature, **by_symbol[symbol]})
    targets = {}
    traces = []
    prices = {r["symbol"]: float(r["close"]) for r in day_rows}
    for strategy_id in selected:
        member = state["members"][strategy_id]
        local = deepcopy(cfg)
        nav = float(member["cash"]) + sum(
            int(p["qty"]) * prices[s] for s, p in member["positions"].items()
        )
        local["portfolio"]["nav_usd"] = nav
        decisions = STRATEGIES[strategy_id](
            features, local, local["strategies"][strategy_id], "CLOSE"
        )
        mapped = {r["symbol"]: r for r in features}
        target = _decision_targets(decisions, mapped, local)
        if strategy_id == "mean_reversion_floor_w1":
            target = _retain_hold_positions(
                target, decisions, member["positions"], mapped, local
            )
        gross = sum(float(spec["weight"]) for spec in target.values())
        if gross > float(local["portfolio"]["max_gross_exposure_pct_nav"]) + 1e-9:
            raise ValueError("STRATEGY_GROSS_EXPOSURE_EXCEEDED")
        targets[strategy_id] = target
        for d in decisions:
            traces.append(
                {
                    "strategy": strategy_id,
                    "symbol": d.symbol,
                    "side": d.side,
                    "qty": d.qty,
                    "reason": d.entry_reason,
                    "trace": d.decision_trace,
                    "m3_context": d.m3_context,
                }
            )
    return targets, traces


def replay_strategy(rows, manifest, plan, output_dir):
    cfg, league, assets, cutoff, dates, symbols = validate_strategy_inputs(
        rows, manifest, plan
    )
    equities = [s for s in symbols if s != "SPY"]
    league = deepcopy(league)
    league["league_id"] = "ai_lab_" + output_dir.parent.name
    league["mode"] = "research_backtest"
    league["members"] = [
        {"id": s, "type": "strategy", "promotion_eligible": False}
        for s in plan.strategies
    ] + [
        {"id": b, "type": "benchmark"}
        for b in ("benchmark_spy", "benchmark_equal_weight")
    ]
    league["strategy_max_holding_sessions"] = {
        s: int(cfg["strategies"][s]["exits"]["temporal_exit_business_days"])
        for s in plan.strategies
    }
    # Do not weaken execution/cost/rebalance rules; drift already rejected.
    contract = {
        "scope": "isolated_ai_lab_retrospective",
        "model_assets": canonical(assets),
        "strategy_config_sha256": digest(
            (REPO / "config/strategies.yaml").read_bytes()
        ),
        "risk_config_sha256": digest((REPO / "config/risk.yaml").read_bytes()),
        "cost_config_sha256": digest((REPO / "config/costs.yaml").read_bytes()),
    }
    output_dir.mkdir(parents=True, exist_ok=False)
    state = None
    predictions = []
    decision_evidence = []
    for day in dates:
        full = forecast_inputs(rows, day, symbols)
        bars = {r["symbol"]: r for r in full}
        if state is None:
            state = initialize_league(
                output_dir, league, day, contract, _benchmark_targets(equities, "SPY")
            )
        else:
            state = advance_league(output_dir, state, league, day, bars, contract, {})
        forecasts = generate_forecasts(
            [r for r in full if r["symbol"] != "SPY"],
            {},
            "CLOSE",
            as_of=instant(full[0]["timestamp"]),
            model_registry_dir=REPO / "data/training/models",
        )
        if forecasts["blocked"] or len(forecasts["forecasts"]) != len(equities):
            raise ValueError("STRATEGY_FORECAST_BLOCKED")
        histories = {
            s: [
                {k: v for k, v in r.items() if k in SOURCE_COLUMNS}
                for r in rows
                if r["symbol"] == s and r["timestamp"][:10] <= day
            ]
            for s in symbols
        }
        targets, traces = close_targets(
            full, forecasts["forecasts"], histories, state, cfg, plan.strategies
        )
        # Preserve genesis benchmark targets; never replace them with forecasts.
        for strategy_id, target in targets.items():
            state["members"][strategy_id]["pending_targets"] = target
        _append_record(output_dir, "RESEARCH_DECISION", state, [], targets)
        predictions.extend(forecasts["forecasts"])
        decision_evidence.append(
            {
                "session": day,
                "available_at": full[0]["timestamp"],
                "decisions": traces,
                "next_open_targets": targets,
            }
        )
    assert state is not None
    final_bars = {r["symbol"]: r for r in forecast_inputs(rows, dates[-1], symbols)}
    liquidations = []
    for member in state["members"].values():
        for symbol, position in list(member["positions"].items()):
            _trade(
                member,
                symbol,
                int(position["qty"]),
                float(final_bars[symbol]["close"]),
                "SELL",
                league["execution"],
                liquidations,
                "research_window_liquidation",
            )
        member["pending_targets"] = None
        member["daily_nav"][-1]["nav"] = round(float(member["cash"]), 6)
    _append_record(output_dir, "RESEARCH_LIQUIDATION", state, liquidations, {})
    leaderboard = build_leaderboard(state, league)
    leaderboard.update(
        mode="research_backtest",
        evidence_type="retrospective_reconstructed",
        automatic_promotion=False,
        live_execution_enabled=False,
    )
    (output_dir / "leaderboard.json").write_text(canonical(leaderboard))
    (output_dir / "decisions.json").write_text(canonical(decision_evidence))
    all_trades = []
    for line in (output_dir / "history.jsonl").read_text().splitlines():
        record = json.loads(line)
        for trade in record["trades"]:
            all_trades.append(
                {**trade, "session": record["session"], "event": record["event"]}
            )
    summary = []
    for r in leaderboard["rows"]:
        summary.append(
            {
                "strategy": r["strategy"],
                "variant": "retrospective_long_only",
                "net_return": r["return"],
                "max_drawdown": r["max_drawdown"],
                "turnover": r["turnover"],
                "trades": r["trades"],
                "costs_paid": r["costs_paid"],
                "sharpe": r["sharpe"],
                "vs_spy": r["vs_spy"],
                "nav": r["nav"],
                "sample_sessions": len(dates),
                "promotion_eligible": False,
            }
        )
    best = max(summary, key=lambda r: r["net_return"])
    reason_counts = {}
    for day in decision_evidence:
        for d in day["decisions"]:
            key = d["strategy"] + "/" + d["side"] + ": " + d["reason"]
            reason_counts[key] = reason_counts.get(key, 0) + 1
    return {
        "schema_version": "1.2",
        "measured_by": "existing FLOOR league engine and canonical strategy generators",
        "dataset_id": plan.dataset_id,
        "temporal_evidence": manifest["temporal_evidence"],
        "comparability": {
            "same_sessions": True,
            "same_initial_nav": True,
            "same_execution_and_costs": True,
            "start": dates[0],
            "end": dates[-1],
            "signal_sessions": len(dates),
            "execution_sessions": len(dates) - 1,
            "cutoff": cutoff.isoformat(),
            "out_of_sample_verified": False,
            "evidence_type": "post_artifact_retrospective_replay",
            "universe": equities,
            "benchmarks": ["SPY", "initial_equal_weight"],
            "end_liquidation": "last close, same costs",
        },
        "cost_contract": {
            "round_trip_cost_bps": round_trip_cost_bps_from_contract(cfg["costs"]),
            "execution": league["execution"],
        },
        "model_assets": assets,
        "metrics": summary,
        "folds": [],
        "feature_audit": [],
        "costs": [],
        "predictions": predictions,
        "trades": all_trades,
        "equity_curves": {
            r["strategy"]: r["equity_curve"] for r in leaderboard["rows"]
        },
        "audit_hash": state["last_hash"],
        "decision_reason_counts": reason_counts,
        "highest_observed_return": {
            "strategy": best["strategy"],
            "net_return": best["net_return"],
        },
        "ranking_leader": {
            "strategy": None,
            "reason": "insufficient independent evidence; descriptive returns are not a validated winner",
            "objective": "net_return",
            "variant": "retrospective_long_only",
        },
        "conclusion": "Retrospective simulated strategy diagnostic; no verified OOS, statistical winner, promotion or real/paper broker execution.",
        "limitations": [
            "Exact training/selection cutoff and historical AI vintages are unavailable.",
            "Daily OHLC stop/take ambiguity uses the existing conservative stop-first engine; no intraday path is claimed.",
            "Liquidity is a completed-bar gate, not an order-book or market-impact model.",
            "At most two equities plus SPY; short survivor subset, overlapping forecasts and last-close liquidation; not official league parity.",
            "Broker kill-switch, daily-loss controls and full-universe sector/portfolio-heat orchestration are not simulated by this research adapter.",
            "No arbitrary new strategy/code, broker connectivity or automatic promotion.",
        ],
    }
