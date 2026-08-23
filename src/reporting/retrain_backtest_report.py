from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from backtest.run_backtest import run_strategy_backtest
from features.run_features import assign_split
from forecasting.parity_models import load_champion_models
from models.evaluate import pinball_loss
from models.run_training import run_training as run_m3_training
from models.train_classic_horizons import run as run_classic_training
from models.train_weekly_opportunity import (
    predict_weekly_opportunity,
    train_weekly_opportunity_model,
)


REQUIRED_COMMON_TARGETS = (
    "floor_d1",
    "ceiling_d1",
    "floor_w1",
    "ceiling_w1",
    "floor_q1",
    "ceiling_q1",
    "floor_m3",
    "floor_week_m3",
    "forward_return_q1",
)
HORIZONS = ("d1", "w1", "q1")


def _day(value: object) -> date:
    text = str(value or "")
    if not text:
        raise ValueError("row missing timestamp")
    return datetime.fromisoformat(text.replace("Z", "+00:00")).date()


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _spearman(a: list[float], b: list[float]) -> float:
    if len(a) < 2 or len(a) != len(b):
        return 0.0

    def ranks(values: list[float]) -> list[float]:
        order = sorted(range(len(values)), key=lambda i: values[i])
        out = [0.0] * len(values)
        for rank, idx in enumerate(order, start=1):
            out[idx] = float(rank)
        return out

    ra = ranks(a)
    rb = ranks(b)
    ma = _mean(ra)
    mb = _mean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    da = math.sqrt(sum((x - ma) ** 2 for x in ra))
    db = math.sqrt(sum((y - mb) ** 2 for y in rb))
    return num / (da * db) if da > 0 and db > 0 else 0.0


def _load_dataset(path: Path) -> tuple[dict, list[dict]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
        return payload, payload["rows"]
    if isinstance(payload, list):
        return {"rows": payload}, payload
    raise ValueError("unsupported modelable dataset payload")


def select_backtest_windows(
    rows: list[dict],
    *,
    window_sessions: int = 21,
) -> dict[str, list[date]]:
    if window_sessions < 5:
        raise ValueError("window_sessions must be >= 5")

    by_date: dict[date, list[dict]] = defaultdict(list)
    symbols = sorted({str(row.get("symbol") or "") for row in rows if row.get("symbol")})
    for row in rows:
        by_date[_day(row.get("timestamp"))].append(row)
    all_dates = sorted(by_date)
    if len(all_dates) < window_sessions:
        raise ValueError("not enough distinct sessions for requested backtest window")

    latest = all_dates[-window_sessions:]

    complete_dates: list[date] = []
    expected = set(symbols)
    for session in all_dates:
        session_rows = by_date[session]
        session_symbols = {str(row.get("symbol") or "") for row in session_rows}
        if session_symbols != expected:
            continue
        if all(
            all(row.get(field) not in (None, "") for field in REQUIRED_COMMON_TARGETS)
            for row in session_rows
        ):
            complete_dates.append(session)

    if len(complete_dates) < window_sessions:
        raise ValueError(
            "not enough fully matured sessions to backtest all horizons; "
            f"have={len(complete_dates)} need={window_sessions}"
        )
    common = complete_dates[-window_sessions:]
    return {"latest": latest, "common_matured": common}


def build_pre_holdout_training_payload(
    payload: dict,
    *,
    holdout_start: date,
) -> dict:
    rows = payload.get("rows", [])
    selected: list[dict] = []
    for row in rows:
        observed = _day(row.get("timestamp"))
        target_end_raw = row.get("target_end_date_m3")
        if observed >= holdout_start or target_end_raw in (None, ""):
            continue
        target_end = date.fromisoformat(str(target_end_raw)[:10])
        if target_end >= holdout_start:
            continue
        selected.append(dict(row))

    if not selected:
        raise ValueError("pre-holdout training set is empty after m3 purge")
    assign_split(selected)

    train_count = sum(row.get("split") == "train" for row in selected)
    valid_m3 = sum(
        row.get("split") == "validation" and row.get("split_eligible_m3") is True
        for row in selected
    )
    if train_count == 0 or valid_m3 == 0:
        raise ValueError(
            "pre-holdout dataset lacks train or leakage-safe m3 validation rows"
        )

    return {
        "rows": selected,
        "final_model_columns": payload.get("final_model_columns", []),
        "split_counts": dict(Counter(str(row.get("split") or "") for row in selected)),
        "backtest_training_contract": {
            "holdout_start": holdout_start.isoformat(),
            "purge_field": "target_end_date_m3",
            "rule": "observed < holdout_start AND target_end_date_m3 < holdout_start",
        },
    }


def _split_train_valid(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    train = [row for row in rows if row.get("split") == "train"]
    valid = [row for row in rows if row.get("split") == "validation"]
    if not train or not valid:
        raise ValueError("training payload requires explicit train and validation splits")
    return train, valid


def train_evaluation_suite(
    training_dataset_path: Path,
    output_dir: Path,
    *,
    version: str,
) -> dict:
    models_dir = output_dir / "models"
    models_dir.mkdir(parents=True, exist_ok=True)

    run_classic_training(
        training_dataset_path,
        models_dir,
        version,
        tasks=["d1", "w1", "q1"],
        training_mode="manual",
    )
    m3_result = run_m3_training(
        training_dataset_path,
        output_dir,
        version=version,
        tasks="value,timing",
        training_mode="manual",
        persistence_db_path=output_dir / "persistence" / "app.sqlite",
    )

    payload = json.loads(training_dataset_path.read_text(encoding="utf-8"))
    train_rows, valid_rows = _split_train_valid(payload["rows"])
    weekly = train_weekly_opportunity_model(
        train_rows,
        valid_rows,
        version=version,
        tune=True,
    )
    weekly_path = models_dir / "weekly_opportunity_challenger.json"
    weekly_path.write_text(
        json.dumps(asdict(weekly), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "models_dir": str(models_dir),
        "weekly_path": str(weekly_path),
        "m3_result": m3_result,
    }


def _classic_metrics(
    rows: list[dict],
    *,
    horizon: str,
    predictor: Any,
    training_rows: list[dict],
) -> dict:
    floor_col = f"floor_{horizon}"
    ceiling_col = f"ceiling_{horizon}"
    eval_rows = [
        row
        for row in rows
        if row.get(floor_col) not in (None, "")
        and row.get(ceiling_col) not in (None, "")
        and float(row.get("close") or 0.0) > 0
    ]
    if not eval_rows:
        return {"status": "not_matured", "rows": 0}

    floor_errors: list[float] = []
    ceiling_errors: list[float] = []
    spread_errors: list[float] = []
    breaches: list[float] = []
    timing_hits: list[float] = []

    train_floor_deltas = []
    train_ceiling_deltas = []
    for row in training_rows:
        close = float(row.get("close") or 0.0)
        floor = row.get(floor_col)
        ceiling = row.get(ceiling_col)
        if close > 0 and floor not in (None, "") and ceiling not in (None, ""):
            train_floor_deltas.append((close - float(floor)) / close)
            train_ceiling_deltas.append((float(ceiling) - close) / close)
    base_floor_delta = _median(train_floor_deltas)
    base_ceiling_delta = _median(train_ceiling_deltas)
    baseline_spread_errors: list[float] = []

    for row in eval_rows:
        close = float(row["close"])
        actual_floor = float(row[floor_col])
        actual_ceiling = float(row[ceiling_col])
        forecast = predictor(row)
        floor_errors.append(abs(forecast.floor - actual_floor) / close)
        ceiling_errors.append(abs(forecast.ceiling - actual_ceiling) / close)
        actual_spread = actual_ceiling - actual_floor
        predicted_spread = forecast.ceiling - forecast.floor
        spread_errors.append(abs(predicted_spread - actual_spread) / close)
        breaches.append(1.0 if actual_floor <= forecast.floor else 0.0)

        base_floor = close * (1.0 - base_floor_delta)
        base_ceiling = close * (1.0 + base_ceiling_delta)
        baseline_spread_errors.append(
            abs((base_ceiling - base_floor) - actual_spread) / close
        )

        if horizon in {"w1", "q1"}:
            actual_time = row.get(f"floor_day_{horizon}")
            if actual_time not in (None, "") and str(forecast.floor_time):
                timing_hits.append(
                    1.0
                    if int(float(str(forecast.floor_time))) == int(actual_time)
                    else 0.0
                )

    model_spread_mae = _mean(spread_errors)
    baseline_spread_mae = _mean(baseline_spread_errors)
    return {
        "status": "ok",
        "rows": len(eval_rows),
        "floor_mae_pct": _mean(floor_errors),
        "ceiling_mae_pct": _mean(ceiling_errors),
        "spread_mae_pct": model_spread_mae,
        "breach_rate": _mean(breaches),
        "floor_timing_accuracy": _mean(timing_hits) if timing_hits else None,
        "baseline_spread_mae_pct": baseline_spread_mae,
        "spread_mae_improvement_vs_baseline": baseline_spread_mae - model_spread_mae,
    }


def _m3_metrics(
    rows: list[dict],
    *,
    modelset: Any,
    training_rows: list[dict],
) -> dict:
    eval_rows = [
        row
        for row in rows
        if row.get("floor_m3") not in (None, "")
        and row.get("floor_week_m3") not in (None, "")
        and float(row.get("close") or 0.0) > 0
    ]
    if not eval_rows:
        return {"status": "not_matured", "rows": 0}

    true_delta: list[float] = []
    pred_delta: list[float] = []
    week_hits: list[float] = []
    top3_hits: list[float] = []
    week_distance: list[float] = []
    breach: list[float] = []

    train_deltas = [
        float(row["floor_delta_m3"])
        for row in training_rows
        if row.get("floor_delta_m3") not in (None, "")
    ]
    baseline_delta = (
        sorted(train_deltas)[
            min(len(train_deltas) - 1, int(0.8 * (len(train_deltas) - 1)))
        ]
        if train_deltas
        else 0.08
    )
    baseline_preds: list[float] = []

    for row in eval_rows:
        close = float(row["close"])
        actual_floor = float(row["floor_m3"])
        actual_delta = (close - actual_floor) / close
        forecast = modelset.predict_m3(row)
        if forecast is None:
            continue
        predicted_delta = (close - float(forecast.floor_m3)) / close
        true_delta.append(actual_delta)
        pred_delta.append(predicted_delta)
        baseline_preds.append(baseline_delta)

        actual_week = int(row["floor_week_m3"])
        predicted_week = int(forecast.floor_week_m3)
        week_hits.append(1.0 if predicted_week == actual_week else 0.0)
        top3_weeks = {int(item["week"]) for item in forecast.floor_week_m3_top3}
        top3_hits.append(1.0 if actual_week in top3_weeks else 0.0)
        week_distance.append(abs(predicted_week - actual_week))
        breach.append(1.0 if actual_floor <= float(forecast.floor_m3) else 0.0)

    if not true_delta:
        return {"status": "not_matured", "rows": 0}

    loss = pinball_loss(true_delta, pred_delta, alpha=0.8)
    baseline_loss = pinball_loss(true_delta, baseline_preds, alpha=0.8)
    return {
        "status": "ok",
        "rows": len(true_delta),
        "pinball_loss_delta": loss,
        "mae_delta": _mean([abs(t - p) for t, p in zip(true_delta, pred_delta)]),
        "breach_rate": _mean(breach),
        "timing_top1_accuracy": _mean(week_hits),
        "timing_top3_accuracy": _mean(top3_hits),
        "expected_week_distance": _mean(week_distance),
        "baseline_pinball_loss_delta": baseline_loss,
        "pinball_improvement_vs_baseline": baseline_loss - loss,
    }


def _opportunity_target(row: dict) -> float | None:
    forward = row.get("forward_return_q1")
    floor = row.get("floor_q1")
    close = float(row.get("close") or 0.0)
    if forward in (None, "") or floor in (None, "") or close <= 0:
        return None
    downside = max(0.01, (close - float(floor)) / close)
    return max(-3.0, min(3.0, float(forward) / downside))


def _opportunity_metrics(rows: list[dict], *, params: dict) -> dict:
    usable = [row for row in rows if _opportunity_target(row) is not None]
    if not usable:
        return {"status": "not_matured", "rows": 0}
    true = [float(_opportunity_target(row) or 0.0) for row in usable]
    pred = [predict_weekly_opportunity(row, params) for row in usable]
    forwards = [float(row.get("forward_return_q1") or 0.0) for row in usable]
    n_top = max(1, math.ceil(0.2 * len(usable)))
    top_idx = sorted(range(len(pred)), key=lambda i: pred[i], reverse=True)[:n_top]
    top_returns = [forwards[i] for i in top_idx]
    all_mean = _mean(forwards)
    return {
        "status": "ok",
        "rows": len(usable),
        "mae_opportunity_score": _mean([abs(t - p) for t, p in zip(true, pred)]),
        "directional_accuracy": _mean(
            [1.0 if (t >= 0) == (p >= 0) else 0.0 for t, p in zip(true, pred)]
        ),
        "spearman_rank_correlation": _spearman(true, pred),
        "top_quintile_mean_forward_return_q1": _mean(top_returns),
        "mean_forward_return_q1": all_mean,
        "top_quintile_return_lift": _mean(top_returns) - all_mean,
    }


def _portfolio_backtest(latest_rows: list[dict], *, params: dict) -> dict:
    by_day: dict[date, list[dict]] = defaultdict(list)
    for row in latest_rows:
        by_day[_day(row["timestamp"])].append(row)
    days = sorted(by_day)
    if len(days) < 3:
        return {"status": "insufficient_rows"}

    all_symbols = sorted({str(row["symbol"]) for row in latest_rows})
    targets: dict[str, dict[str, float]] = {}
    for idx, session in enumerate(days[:-1]):
        scores = [
            (str(row["symbol"]), predict_weekly_opportunity(row, params))
            for row in by_day[session]
        ]
        scores.sort(key=lambda item: item[1], reverse=True)
        positive = [item for item in scores if item[1] > 0]
        top_n = max(1, math.ceil(len(scores) * 0.20)) if scores else 0
        selected = positive[:top_n]
        next_day = days[idx + 1].isoformat()
        day_targets = {symbol: 0.0 for symbol in all_symbols}
        if selected:
            weight = min(1.0 / len(selected), 0.20)
            for symbol, _ in selected:
                day_targets[symbol] = weight
        targets[next_day] = day_targets

    market_data = []
    for session in days:
        for row in by_day[session]:
            market_data.append(
                {
                    "date": session.isoformat(),
                    "ticker": str(row["symbol"]),
                    "open": float(row.get("open") or row["close"]),
                    "high": float(row.get("high") or row["close"]),
                    "low": float(row.get("low") or row["close"]),
                    "close": float(row["close"]),
                    "volume": float(row.get("volume") or 0.0),
                }
            )

    config = {
        "costs": {
            "commission_bps": 2.0,
            "slippage_bps": 2.0,
            "sell_fee_bps": 3.0,
            "min_commission": 0.0,
        },
        "execution": {"max_participation_rate": 0.10, "price_reference": "ohlc4"},
        "portfolio": {
            "initial_cash": 100_000.0,
            "max_gross_exposure": 1.0,
            "allow_short": False,
            "strategy_weights": {"weekly_opportunity": 1.0},
        },
        "horizons": [5, 10, 20],
    }
    result = run_strategy_backtest(market_data, "weekly_opportunity", targets, config)
    curve = result["equity_curve"]
    total_return = (
        float(curve[-1]["equity"]) / float(curve[0]["equity"]) - 1.0
        if len(curve) >= 2 and float(curve[0]["equity"]) > 0
        else 0.0
    )

    benchmark_day = days[1].isoformat()
    benchmark_symbols = sorted({str(row["symbol"]) for row in by_day[days[1]]})
    benchmark_weight = 1.0 / len(benchmark_symbols) if benchmark_symbols else 0.0
    benchmark_targets = {
        benchmark_day: {symbol: benchmark_weight for symbol in benchmark_symbols}
    }
    benchmark = run_strategy_backtest(
        market_data,
        "equal_weight_buy_hold",
        benchmark_targets,
        {
            **config,
            "portfolio": {
                **config["portfolio"],
                "strategy_weights": {"equal_weight_buy_hold": 1.0},
            },
        },
    )
    benchmark_curve = benchmark["equity_curve"]
    benchmark_return = (
        float(benchmark_curve[-1]["equity"]) / float(benchmark_curve[0]["equity"]) - 1.0
        if len(benchmark_curve) >= 2 and float(benchmark_curve[0]["equity"]) > 0
        else 0.0
    )
    return {
        "status": "ok",
        "signal_to_trade_lag": "1 session",
        "transaction_costs_included": True,
        "total_return": total_return,
        "equal_weight_buy_hold_return": benchmark_return,
        "excess_return_vs_equal_weight": total_return - benchmark_return,
        "trade_count": len(result.get("trades", [])),
        "metrics": result["metrics"],
        "benchmark_metrics": benchmark["metrics"],
    }


def _window_rows(rows: list[dict], sessions: list[date]) -> list[dict]:
    allowed = set(sessions)
    return [row for row in rows if _day(row.get("timestamp")) in allowed]


def build_backtest_report(
    full_rows: list[dict],
    training_rows: list[dict],
    *,
    models_dir: Path,
    windows: dict[str, list[date]],
    weekly_params: dict,
    version: str,
) -> dict:
    modelset = load_champion_models(models_dir)
    if not modelset.is_available:
        raise RuntimeError(f"evaluation model suite incomplete: {modelset.load_diagnostics}")

    report_windows: dict[str, dict] = {}
    for name, sessions in windows.items():
        rows = _window_rows(full_rows, sessions)
        report_windows[name] = {
            "start": sessions[0].isoformat(),
            "end": sessions[-1].isoformat(),
            "sessions": len(sessions),
            "rows": len(rows),
            "models": {
                "d1": _classic_metrics(
                    rows,
                    horizon="d1",
                    predictor=modelset.predict_d1,
                    training_rows=training_rows,
                ),
                "w1": _classic_metrics(
                    rows,
                    horizon="w1",
                    predictor=modelset.predict_w1,
                    training_rows=training_rows,
                ),
                "q1": _classic_metrics(
                    rows,
                    horizon="q1",
                    predictor=modelset.predict_q1,
                    training_rows=training_rows,
                ),
                "m3": _m3_metrics(rows, modelset=modelset, training_rows=training_rows),
                "weekly_opportunity": _opportunity_metrics(rows, params=weekly_params),
            },
        }

    latest_rows = _window_rows(full_rows, windows["latest"])
    report_windows["latest"]["portfolio_backtest"] = _portfolio_backtest(
        latest_rows,
        params=weekly_params,
    )
    return {
        "schema_version": 1,
        "version": version,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "methodology": {
            "evaluation_fit": (
                "all evaluation models are trained only on rows whose m3 target "
                "ends before the common holdout starts"
            ),
            "common_matured_window": (
                "last 21 sessions for which all d1/w1/q1/m3/opportunity targets "
                "are fully observed for every symbol"
            ),
            "latest_window": (
                "last 21 available sessions; each model is scored only on rows "
                "whose realized target is already known"
            ),
            "portfolio": (
                "weekly opportunity scores at session t are traded at t+1 with "
                "2 bps commission, 2 bps slippage, 3 bps sell fee and 10% max participation"
            ),
            "test_holdout_used_for_training": False,
            "live_or_paper_execution_enabled": False,
        },
        "windows": report_windows,
    }


def write_report_files(report: dict, output_dir: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "backtest_report.json"
    md_path = output_dir / "backtest_report.md"
    csv_path = output_dir / "backtest_metrics.csv"

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# Retrain + backtest report",
        "",
        f"- Version: `{report['version']}`",
        f"- Generated: `{report['generated_at']}`",
        "",
    ]
    csv_rows = []
    for window_name, window in report["windows"].items():
        lines.extend(
            [
                f"## {window_name}",
                f"- Window: `{window['start']}` → `{window['end']}` ({window['sessions']} sessions)",
                "",
                "| Model | Status | Rows | Primary metric | vs baseline |",
                "|---|---:|---:|---:|---:|",
            ]
        )
        for model, metrics in window["models"].items():
            status = metrics.get("status", "unknown")
            rows = metrics.get("rows", 0)
            if model in HORIZONS:
                primary = metrics.get("spread_mae_pct")
                delta = metrics.get("spread_mae_improvement_vs_baseline")
            elif model == "m3":
                primary = metrics.get("pinball_loss_delta")
                delta = metrics.get("pinball_improvement_vs_baseline")
            else:
                primary = metrics.get("spearman_rank_correlation")
                delta = metrics.get("top_quintile_return_lift")
            ptxt = "N/A" if primary is None else f"{float(primary):.6f}"
            dtxt = "N/A" if delta is None else f"{float(delta):.6f}"
            lines.append(f"| {model} | {status} | {rows} | {ptxt} | {dtxt} |")
            csv_rows.append(
                {
                    "window": window_name,
                    "model": model,
                    "status": status,
                    "rows": rows,
                    "primary_metric": primary,
                    "vs_baseline_or_lift": delta,
                }
            )
        portfolio = window.get("portfolio_backtest")
        if isinstance(portfolio, dict):
            lines.extend(
                [
                    "",
                    "### Portfolio simulation",
                    f"- Status: `{portfolio.get('status')}`",
                    f"- Total return: `{float(portfolio.get('total_return') or 0.0):.4%}`",
                    f"- Equal-weight buy/hold: `{float(portfolio.get('equal_weight_buy_hold_return') or 0.0):.4%}`",
                    f"- Excess vs equal-weight: `{float(portfolio.get('excess_return_vs_equal_weight') or 0.0):.4%}`",
                    f"- Trades: `{portfolio.get('trade_count', 0)}`",
                    "- Signal is shifted one session before execution; transaction costs are included.",
                ]
            )
        lines.append("")

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "window",
                "model",
                "status",
                "rows",
                "primary_metric",
                "vs_baseline_or_lift",
            ],
        )
        writer.writeheader()
        writer.writerows(csv_rows)

    return {"json": str(json_path), "markdown": str(md_path), "csv": str(csv_path)}


def _write_weekly_artifact_for_final(
    dataset_path: Path,
    output_path: Path,
    *,
    version: str,
) -> None:
    payload = json.loads(dataset_path.read_text(encoding="utf-8"))
    train_rows, valid_rows = _split_train_valid(payload["rows"])
    artifact = train_weekly_opportunity_model(
        train_rows,
        valid_rows,
        version=version,
        tune=True,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(asdict(artifact), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def run_pipeline(
    dataset_path: Path,
    output_root: Path,
    *,
    version: str,
    window_sessions: int = 21,
    final_fit: bool = True,
) -> dict:
    payload, rows = _load_dataset(dataset_path)
    windows = select_backtest_windows(rows, window_sessions=window_sessions)
    common_start = windows["common_matured"][0]

    eval_root = output_root / "evaluation_fit"
    training_payload = build_pre_holdout_training_payload(
        payload,
        holdout_start=common_start,
    )
    training_dataset_path = eval_root / "modelable_pre_holdout.json"
    training_dataset_path.parent.mkdir(parents=True, exist_ok=True)
    training_dataset_path.write_text(
        json.dumps(training_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    train_result = train_evaluation_suite(
        training_dataset_path,
        eval_root,
        version=f"{version}-eval",
    )
    weekly_payload = json.loads(Path(train_result["weekly_path"]).read_text(encoding="utf-8"))
    report = build_backtest_report(
        rows,
        training_payload["rows"],
        models_dir=Path(train_result["models_dir"]),
        windows=windows,
        weekly_params=weekly_payload["params"],
        version=version,
    )
    report_paths = write_report_files(report, output_root / "report")

    final_root = output_root / "final_fit"
    final_summary: dict[str, object] = {"enabled": final_fit}
    if final_fit:
        run_classic_training(
            dataset_path,
            final_root / "models",
            version,
            tasks=["d1", "w1", "q1"],
            training_mode="manual",
        )
        m3_result = run_m3_training(
            dataset_path,
            final_root,
            version=version,
            tasks="value,timing",
            training_mode="manual",
            persistence_db_path=final_root / "persistence" / "app.sqlite",
        )
        weekly_final_path = final_root / "models" / "weekly_opportunity_challenger.json"
        _write_weekly_artifact_for_final(
            dataset_path,
            weekly_final_path,
            version=version,
        )
        final_summary.update(
            {
                "models_dir": str(final_root / "models"),
                "weekly_challenger": str(weekly_final_path),
                "m3_result": m3_result,
                "promotion_applied": False,
            }
        )

    summary = {
        "version": version,
        "window_sessions": window_sessions,
        "common_holdout_start": common_start.isoformat(),
        "evaluation_fit": train_result,
        "report_paths": report_paths,
        "final_fit": final_summary,
    }
    summary_path = output_root / "run_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Leakage-safe retrain, monthly backtest and report orchestration"
    )
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--window-sessions", type=int, default=21)
    parser.add_argument(
        "--skip-final-fit",
        action="store_true",
        help="Backtest only; do not train final full-dataset candidate artifacts",
    )
    args = parser.parse_args()
    result = run_pipeline(
        Path(args.dataset),
        Path(args.output_root),
        version=args.version,
        window_sessions=args.window_sessions,
        final_fit=not args.skip_final_fit,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
