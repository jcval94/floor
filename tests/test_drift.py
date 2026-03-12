from __future__ import annotations

import json
from pathlib import Path

from monitoring.run_retrain_assessment import load_simple_yaml, run_assessment
from monitoring.retraining_report import append_history, build_retraining_report


def _rows(n: int, shift: float = 0.0) -> list[dict]:
    out = []
    for i in range(n):
        out.append(
            {
                "ret_lag_1": 0.01 * i + shift,
                "rolling_vol_20": 0.2 + 0.001 * i + shift,
                "atr_14": 1.0 + 0.01 * i + shift,
                "relative_volume_20": 1.0 + 0.005 * i,
                "beta_20": 1.1 + 0.001 * i,
                "vol_regime_score": 1.0 + 0.01 * (i % 3),
                "rsi_14": 50 + i % 10,
                "macd_histogram": -0.1 + 0.01 * i,
                "vwap_distance": 0.002 * i,
                "ai_consensus_score": 0.6,
                "floor_d1": 95 + i + shift,
                "ceiling_d1": 105 + i + shift,
                "floor_w1": 94 + i + shift,
                "ceiling_w1": 106 + i + shift,
                "floor_q1": 93 + i + shift,
                "ceiling_q1": 107 + i + shift,
                "floor_time_bucket_d1": "OPEN" if i % 2 == 0 else "OPEN_PLUS_2H",
                "ceiling_time_bucket_d1": "CLOSE" if i % 2 == 0 else "OPEN_PLUS_4H",
                "floor_day_w1": (i % 5) + 1,
                "ceiling_day_w1": ((i + 1) % 5) + 1,
                "floor_day_q1": (i % 10) + 1,
                "ceiling_day_q1": ((i + 2) % 10) + 1,
            }
        )
    return out


def _pack(rows: list[dict], remove_col: bool = False) -> dict:
    cols = list(rows[0].keys())
    if remove_col:
        cols = [c for c in cols if c != "beta_20"]
    return {
        "rows": rows,
        "coverage": {"coverage_error": 0.02, "calibration_error": 0.03},
        "schema": {
            "columns": cols,
            "coverage_by_column": {c: (0.95 if c != "ai_consensus_score" else 0.92) for c in cols},
        },
        "performance": {"pinball_loss": 0.10, "breach_rate": 0.20},
    }


def _cfg() -> dict:
    return {
        "important_features": {
            "columns": "ret_lag_1,rolling_vol_20,atr_14,relative_volume_20,beta_20,vol_regime_score,rsi_14,macd_histogram,vwap_distance,ai_consensus_score"
        },
        "thresholds": {
            "feature_psi_warn": 0.12,
            "feature_psi_fail": 0.25,
            "target_js_warn": 0.06,
            "target_js_fail": 0.12,
            "temporal_js_warn": 0.05,
            "temporal_js_fail": 0.10,
            "coverage_calibration_warn": 0.03,
            "coverage_calibration_fail": 0.06,
            "min_column_coverage": 0.90,
        },
        "performance_thresholds": {
            "pinball_loss_warn": 0.02,
            "pinball_loss_fail": 0.05,
            "breach_rate_warn": 0.03,
            "breach_rate_fail": 0.06,
        },
        "timing_thresholds": {
            "accuracy_drop_warn": 0.03,
            "accuracy_drop_fail": 0.06,
            "log_loss_warn": 0.04,
            "log_loss_fail": 0.08,
            "brier_warn": 0.02,
            "brier_fail": 0.05,
            "timing_distance_warn": 0.40,
            "timing_distance_fail": 0.80,
        },
        "paper_thresholds": {
            "return_drop_warn": 0.02,
            "return_drop_fail": 0.05,
            "drawdown_increase_warn": 0.03,
            "drawdown_increase_fail": 0.06,
            "sharpe_drop_warn": 0.20,
            "sharpe_drop_fail": 0.50,
        },
    }


def test_retraining_decision_red_and_history(tmp_path: Path) -> None:
    ref = _pack(_rows(60, shift=0.0))
    cur = _pack(_rows(60, shift=3.0), remove_col=True)
    cur["coverage"] = {"coverage_error": 0.12, "calibration_error": 0.14}
    cur["performance"] = {"pinball_loss": 0.18, "breach_rate": 0.30}

    timing_ref = {"accuracy": 0.62, "log_loss": 0.61, "brier_score": 0.21, "timing_distance": 1.3}
    timing_cur = {"accuracy": 0.52, "log_loss": 0.75, "brier_score": 0.30, "timing_distance": 2.4}
    paper_ref = {"strategy_return": 0.11, "max_drawdown": 0.10, "sharpe": 1.3}
    paper_cur = {"strategy_return": 0.02, "max_drawdown": 0.22, "sharpe": 0.6}

    decision = run_assessment(ref, cur, timing_ref, timing_cur, paper_ref, paper_cur, _cfg())
    assert decision["traffic_light"] == "RED"
    assert decision["recommendation"] == "RETRAIN_NOW"

    report = build_retraining_report(decision, {"reference_rows": 60, "current_rows": 60}, _cfg())
    hist = tmp_path / "reviews.jsonl"
    append_history(hist, report)
    lines = hist.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["decision"]["recommendation"] == "RETRAIN_NOW"


def test_retraining_decision_green() -> None:
    ref = _pack(_rows(60, shift=0.0))
    cur = _pack(_rows(60, shift=0.0))
    cur["coverage"] = {"coverage_error": 0.025, "calibration_error": 0.031}
    cur["performance"] = {"pinball_loss": 0.108, "breach_rate": 0.205}

    timing_ref = {"accuracy": 0.62, "log_loss": 0.61, "brier_score": 0.21, "timing_distance": 1.3}
    timing_cur = {"accuracy": 0.615, "log_loss": 0.615, "brier_score": 0.212, "timing_distance": 1.35}
    paper_ref = {"strategy_return": 0.11, "max_drawdown": 0.10, "sharpe": 1.3}
    paper_cur = {"strategy_return": 0.105, "max_drawdown": 0.11, "sharpe": 1.2}

    decision = run_assessment(ref, cur, timing_ref, timing_cur, paper_ref, paper_cur, _cfg())
    assert decision["traffic_light"] == "GREEN"
    assert decision["recommendation"] == "SKIP_RETRAIN"


def test_load_simple_yaml() -> None:
    cfg = load_simple_yaml(Path("config/retraining.yaml"))
    assert cfg["review"]["cadence_days"] == 14
    assert "feature_psi_fail" in cfg["thresholds"]
