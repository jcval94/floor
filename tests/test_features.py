from __future__ import annotations

from datetime import datetime, timedelta

from features.run_features import build_modelable_dataset
from features.feature_builder import build_features


def _next_business_day(d: datetime) -> datetime:
    x = d + timedelta(days=1)
    while x.weekday() >= 5:
        x += timedelta(days=1)
    return x


def _synthetic_rows(days: int = 110) -> list[dict]:
    start = datetime(2024, 1, 2, 9, 30)
    rows: list[dict] = []
    price = 100.0
    bench = 400.0
    day_start = start
    produced = 0
    while produced < days:
        if day_start.weekday() < 5:
            for bucket in range(5):
                ts = day_start + timedelta(hours=2 * bucket)
                drift = 0.12 * produced + 0.08 * bucket
                close = price + drift
                row = {
                    "symbol": "AAPL",
                    "timestamp": ts.isoformat(),
                    "open": close - 0.2,
                    "high": close + (1.2 if bucket == 1 else 0.5),
                    "low": close - (1.1 if bucket == 2 else 0.4),
                    "close": close,
                    "volume": 1_000 + produced * 10 + bucket,
                    "benchmark_close": bench + 0.15 * produced + 0.05 * bucket,
                    "ai_action": "BUY",
                    "ai_conviction": 0.7,
                    "ai_floor_d1": close - 1.0,
                    "ai_ceiling_d1": close + 1.0,
                    "ai_floor_w1": close - 2.0,
                    "ai_ceiling_w1": close + 2.0,
                    "ai_floor_q1": close - 3.0,
                    "ai_ceiling_q1": close + 3.0,
                    "ai_floor_m3": close - 5.0,
                    "ai_conviction_long": 0.66,
                    "ai_recency_long": 2,
                    "ai_consensus_score": 0.65,
                    "ai_updated_at": (ts - timedelta(days=1)).isoformat(),
                }
                rows.append(row)
            price += 0.25
            bench += 0.1
            produced += 1
        day_start += timedelta(days=1)
    return rows


def test_feature_and_label_outputs_present() -> None:
    artifact = build_modelable_dataset(_synthetic_rows())
    rows = artifact["rows"]
    sample = rows[80]

    assert sample["floor_d1"] is not None
    assert sample["ceiling_d1"] is not None
    assert sample["floor_w1"] is not None
    assert sample["ceiling_w1"] is not None
    assert sample["floor_q1"] is not None
    assert sample["ceiling_q1"] is not None
    assert sample["floor_m3"] is not None
    assert sample["realized_floor_m3"] is not None

    assert sample["floor_time_bucket_d1"] in {"OPEN", "OPEN_PLUS_2H", "OPEN_PLUS_4H", "OPEN_PLUS_6H", "CLOSE"}
    assert sample["ceiling_time_bucket_d1"] in {"OPEN", "OPEN_PLUS_2H", "OPEN_PLUS_4H", "OPEN_PLUS_6H", "CLOSE"}
    assert sample["floor_day_w1"] in {1, 2, 3, 4, 5}
    assert sample["ceiling_day_w1"] in {1, 2, 3, 4, 5}
    assert sample["floor_day_q1"] in {1, 2, 3, 4, 5, 6, 7, 8, 9, 10}
    assert sample["ceiling_day_q1"] in {1, 2, 3, 4, 5, 6, 7, 8, 9, 10}
    assert sample["floor_week_m3"] in set(range(1, 14))
    assert sample["floor_week_m3_start_date"] <= sample["floor_week_m3_end_date"]


def test_no_leakage_for_lagged_return() -> None:
    rows = _synthetic_rows()
    artifact = build_modelable_dataset(rows)
    out_rows = artifact["rows"]

    i = 10
    prev_close = out_rows[i - 1]["close"]
    curr_close = out_rows[i]["close"]
    expected = curr_close / prev_close - 1.0
    assert abs(out_rows[i]["ret_lag_1"] - expected) < 1e-12


def test_split_and_walk_forward_exist() -> None:
    artifact = build_modelable_dataset(_synthetic_rows())
    splits = {r["split"] for r in artifact["rows"]}
    assert {"train", "validation", "test"}.issubset(splits)
    assert isinstance(artifact["walk_forward_folds"], list)
    assert artifact["missingness_report"]
    assert artifact["feature_registry"]
    assert "m3" in artifact["horizon_coverage"]


def test_model_competition_has_four_models_including_lstm_xgboost_and_evt_per_horizon() -> None:
    artifact = build_modelable_dataset(_synthetic_rows())
    competition = artifact["model_competition"]["models_by_horizon"]
    for horizon in ("d1", "w1", "q1"):
        models = competition[horizon]
        assert len(models) == 4
        families = {m["model_family"] for m in models}
        assert "lstm_sequence" in families
        assert "xgboost" in families
        assert "evt_changepoint_hybrid" in families


def test_new_key_indicators_present() -> None:
    artifact = build_modelable_dataset(_synthetic_rows())
    sample = artifact["rows"][250]
    for col in [
        "rsi_14",
        "macd_12_26",
        "macd_signal_9",
        "macd_histogram",
        "bollinger_width_20",
        "vwap_distance",
        "parkinson_vol_20",
        "momentum_10",
        "momentum_20",
        "trend_context_m3",
        "slope_4w",
        "slope_8w",
        "slope_13w",
        "drawdown_13w",
        "range_compression_20_60",
        "rel_strength_13w",
        "dist_to_low_3m",
        "vol_persistence_20_60",
        "ai_floor_m3",
        "ai_conviction_long",
        "ai_recency_long",
        "ai_horizon_alignment",
    ]:
        assert col in sample


def test_m3_tie_break_selects_earliest_week() -> None:
    start = datetime(2024, 1, 2, 9, 30)
    rows: list[dict] = []
    day = start
    produced = 0
    while produced < 80:
        if day.weekday() < 5:
            low = 100.0 + produced
            if produced in {5, 6}:  # week 1 in forward window (current day excluded)
                low = 50.0
            if produced in {15, 16}:  # week 3 same minimum -> tie
                low = 50.0
            rows.append(
                {
                    "symbol": "AAPL",
                    "timestamp": day.isoformat(),
                    "open": 100.0,
                    "high": 102.0,
                    "low": low,
                    "close": 101.0,
                    "volume": 1_000,
                    "benchmark_close": 400.0,
                }
            )
            produced += 1
        day += timedelta(days=1)

    artifact = build_modelable_dataset(rows)
    anchor = artifact["rows"][0]
    assert anchor["floor_m3"] == 50.0
    assert anchor["floor_week_m3"] == 1


def test_m3_target_documentation_exists() -> None:
    artifact = build_modelable_dataset(_synthetic_rows())
    docs = artifact["target_documentation"]["m3_target"]
    assert "tie_break_rule" in docs
    assert "week_assignment" in docs


def test_build_features_uses_close_when_benchmark_close_missing() -> None:
    rows = [
        {
            "symbol": "AAPL",
            "timestamp": "2024-01-02T09:30:00",
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 1_000,
            "benchmark_close": None,
        },
        {
            "symbol": "AAPL",
            "timestamp": "2024-01-02T11:30:00",
            "open": 100.5,
            "high": 102.0,
            "low": 100.0,
            "close": 101.0,
            "volume": 1_100,
            "benchmark_close": None,
        },
    ]

    featured = build_features(rows)
    assert len(featured) == 2
    assert featured[0]["ret_lag_1"] is None
    assert featured[1]["ret_lag_1"] == rows[1]["close"] / rows[0]["close"] - 1.0
