from __future__ import annotations

from datetime import date, timedelta
import math

from models.adaptive_net_utility import (
    FEATURE_NAMES,
    HYPERPARAMETER_GRID,
    _fit_head,
    feature_map,
    predict_return,
    selection_metrics,
    train_and_audit,
)


def _row(day: date, symbol_idx: int, split: str) -> dict:
    phase = (day.toordinal() % 17) / 17.0
    momentum = 0.06 * math.sin(phase * 2.0 * math.pi) + 0.004 * symbol_idx
    relative = 0.04 * math.cos(phase * 2.0 * math.pi) - 0.003 * symbol_idx
    trend = 0.5 * momentum + 0.3 * relative
    drawdown = -abs(0.04 * math.sin(phase * math.pi))
    position = min(1.0, max(0.0, 0.5 + 4.0 * momentum - 2.0 * relative))
    # Deliberately nonlinear target: a shallow tree can exploit the interaction
    # while a six-feature linear Ridge cannot represent it exactly.
    regime_bonus = 0.025 if momentum * relative > 0 else -0.018
    future = 0.55 * momentum + 0.35 * relative + regime_bonus
    return {
        "symbol": f"S{symbol_idx}",
        "timestamp": day.isoformat(),
        "target_end_date_q1": (day + timedelta(days=14)).isoformat(),
        "split": split,
        "split_eligible_q1": True,
        "close": 100.0 + symbol_idx,
        "atr_14": 2.0 + 0.1 * symbol_idx,
        "momentum_20": momentum,
        "rel_strength_20": relative,
        "trend_context_m3": trend,
        "drawdown_13w": drawdown,
        "price_position_in_range_20": position,
        "floor_q1": 90.0,
        "forward_return_q1": future,
    }


def _dataset() -> list[dict]:
    start = date(2025, 1, 2)
    rows: list[dict] = []
    for offset in range(150):
        day = start + timedelta(days=offset)
        if offset < 100:
            split = "train"
        elif offset < 125:
            split = "validation"
        else:
            split = "test"
        for symbol_idx in range(6):
            rows.append(_row(day, symbol_idx, split))
    return rows


def test_feature_contract_is_small_and_runtime_safe() -> None:
    row = _row(date(2026, 1, 5), 1, "train")
    features = feature_map(row)

    assert tuple(features) == FEATURE_NAMES
    assert features["atr_ratio_14"] == row["atr_14"] / row["close"]
    assert all(math.isfinite(value) for value in features.values())


def test_exported_booster_predicts_without_sklearn_runtime_contract() -> None:
    rows = _dataset()[:300]
    head = _fit_head(rows, dict(HYPERPARAMETER_GRID[0]))
    prediction = predict_return(rows[-1], head)

    assert head["kind"] == "hist_gradient_boosting"
    assert head["features"] == list(FEATURE_NAMES)
    assert -0.20 <= prediction <= 0.20


def test_selection_metrics_charge_round_trip_cost() -> None:
    rows = [_row(date(2026, 2, 2), idx, "validation") for idx in range(10)]
    predictions = [float(row["forward_return_q1"]) for row in rows]
    metrics = selection_metrics(
        rows,
        predictions,
        tail_fraction=0.20,
        min_abs_prediction=0.0,
        round_trip_cost=0.0058,
    )

    assert metrics.trades > 0
    assert metrics.dates == 1
    assert metrics.mean_net_return < max(abs(value) for value in predictions)


def test_full_audit_keeps_blind_test_out_of_selection() -> None:
    report = train_and_audit(_dataset(), version="synthetic-test")

    assert report["selection"]["test_used_for_selection"] is False
    assert report["selection"]["blind_test_opened_after_freeze"] is True
    assert report["params"]["canonical_serving_enabled"] is False
    assert report["params"]["paper_enabled"] is False
    assert report["params"]["live_enabled"] is False
    assert report["metrics"]["validation"]["adaptive"]["trades"] > 0
    assert report["metrics"]["blind_test"]["adaptive"]["trades"] > 0
