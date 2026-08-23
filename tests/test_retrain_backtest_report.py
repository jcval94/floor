from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from models.train_weekly_opportunity import FEATURE_NAMES
from reporting.retrain_backtest_report import (
    _portfolio_backtest,
    build_pre_holdout_training_payload,
    select_backtest_windows,
    write_report_files,
)


def _rows(days: int = 140, complete_through: int = 110) -> list[dict]:
    start = datetime(2025, 1, 2, 16, 0)
    rows: list[dict] = []
    for idx in range(days):
        ts = start + timedelta(days=idx)
        complete = idx < complete_through
        close = 100.0 + idx * 0.2
        row = {
            "timestamp": ts.isoformat(),
            "symbol": "AAA",
            "open": close - 0.2,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 1_000_000,
            "momentum_20": 0.02,
            "rel_strength_20": 0.01,
            "trend_context_m3": 0.03,
            "drawdown_13w": -0.04,
            "atr_14": 1.5,
            "price_position_in_range_20": 0.6,
            "floor_d1": close - 1.0,
            "ceiling_d1": close + 1.0,
            "floor_w1": close - 2.0,
            "ceiling_w1": close + 2.0,
            "floor_q1": close - 3.0,
            "ceiling_q1": close + 3.0,
            "forward_return_q1": 0.03,
            "floor_day_w1": 2,
            "floor_day_q1": 4,
            "horizon_complete_d1": True,
            "horizon_complete_w1": True,
            "horizon_complete_q1": True,
            "target_end_date_d1": (ts + timedelta(days=1)).date().isoformat(),
            "target_end_date_w1": (ts + timedelta(days=2)).date().isoformat(),
            "target_end_date_q1": (ts + timedelta(days=3)).date().isoformat(),
        }
        if complete:
            row.update(
                {
                    "floor_m3": close - 8.0,
                    "floor_delta_m3": 8.0 / close,
                    "floor_week_m3": 6,
                    "horizon_complete_m3": True,
                    "target_end_date_m3": (ts + timedelta(days=4)).date().isoformat(),
                }
            )
        else:
            row.update(
                {
                    "floor_m3": None,
                    "floor_delta_m3": None,
                    "floor_week_m3": None,
                    "horizon_complete_m3": False,
                    "target_end_date_m3": None,
                }
            )
        rows.append(row)
    return rows


def test_windows_keep_latest_literal_month_separate_from_fully_matured_month() -> None:
    rows = _rows()
    windows = select_backtest_windows(rows, window_sessions=21)
    assert len(windows["latest"]) == 21
    assert len(windows["common_matured"]) == 21
    assert windows["common_matured"][-1] < windows["latest"][0]


def test_pre_holdout_training_purges_any_m3_label_crossing_holdout() -> None:
    rows = _rows(days=120, complete_through=120)
    holdout_start = datetime.fromisoformat(rows[100]["timestamp"]).date()
    payload = build_pre_holdout_training_payload(
        {"rows": rows, "final_model_columns": sorted(rows[0])},
        holdout_start=holdout_start,
    )
    assert payload["rows"]
    for row in payload["rows"]:
        observed = datetime.fromisoformat(row["timestamp"]).date()
        target_end = datetime.fromisoformat(row["target_end_date_m3"]).date()
        assert observed < holdout_start
        assert target_end < holdout_start
    assert payload["backtest_training_contract"]["purge_field"] == "target_end_date_m3"
    assert any(
        row["split"] == "validation" and row["split_eligible_m3"]
        for row in payload["rows"]
    )


def test_portfolio_backtest_delays_signal_and_reports_equal_weight_baseline() -> None:
    rows = _rows(days=8, complete_through=8)
    params = {
        "model_type": "ridge_risk_adjusted_ranker",
        "feature_names": list(FEATURE_NAMES),
        "feature_means": [0.0] * len(FEATURE_NAMES),
        "feature_scales": [1.0] * len(FEATURE_NAMES),
        "weights": [0.0] * len(FEATURE_NAMES),
        "bias": 1.0,
    }
    result = _portfolio_backtest(rows, params=params)
    assert result["status"] == "ok"
    assert result["signal_to_trade_lag"] == "1 session"
    assert result["transaction_costs_included"] is True
    assert "equal_weight_buy_hold_return" in result
    assert "excess_return_vs_equal_weight" in result


def test_report_files_emit_json_markdown_and_csv(tmp_path: Path) -> None:
    report = {
        "version": "vtest",
        "generated_at": "2026-08-23T00:00:00Z",
        "windows": {
            "common_matured": {
                "start": "2026-01-01",
                "end": "2026-01-31",
                "sessions": 21,
                "models": {
                    "d1": {"status": "ok", "rows": 21, "spread_mae_pct": 0.01, "spread_mae_improvement_vs_baseline": 0.002},
                    "w1": {"status": "ok", "rows": 21, "spread_mae_pct": 0.02, "spread_mae_improvement_vs_baseline": 0.001},
                    "q1": {"status": "ok", "rows": 21, "spread_mae_pct": 0.03, "spread_mae_improvement_vs_baseline": 0.001},
                    "m3": {"status": "ok", "rows": 21, "pinball_loss_delta": 0.01, "pinball_improvement_vs_baseline": 0.003},
                    "weekly_opportunity": {"status": "ok", "rows": 21, "spearman_rank_correlation": 0.4, "top_quintile_return_lift": 0.02},
                },
            }
        },
    }
    paths = write_report_files(report, tmp_path)
    for path in paths.values():
        assert Path(path).exists()
    payload = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
    assert payload["version"] == "vtest"
    assert "weekly_opportunity" in Path(paths["markdown"]).read_text(encoding="utf-8")
