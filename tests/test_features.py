from __future__ import annotations

from datetime import datetime, timedelta

from features.run_features import build_modelable_dataset


def _synthetic_rows() -> list[dict]:
    start = datetime(2024, 1, 2, 9, 30)
    rows: list[dict] = []
    price = 100.0
    bench = 400.0
    for day in range(18):
        day_start = start + timedelta(days=day)
        for bucket in range(5):
            ts = day_start + timedelta(hours=2 * bucket)
            drift = 0.2 * day + 0.1 * bucket
            close = price + drift
            row = {
                "symbol": "AAPL",
                "timestamp": ts.isoformat(),
                "open": close - 0.2,
                "high": close + (1.2 if bucket == 1 else 0.5),
                "low": close - (1.1 if bucket == 2 else 0.4),
                "close": close,
                "volume": 1_000 + day * 10 + bucket,
                "benchmark_close": bench + 0.15 * day + 0.05 * bucket,
                "ai_action": "BUY",
                "ai_conviction": 0.7,
                "ai_floor_d1": close - 1.0,
                "ai_ceiling_d1": close + 1.0,
                "ai_floor_w1": close - 2.0,
                "ai_ceiling_w1": close + 2.0,
                "ai_floor_q1": close - 3.0,
                "ai_ceiling_q1": close + 3.0,
                "ai_consensus_score": 0.65,
                "ai_updated_at": (ts - timedelta(days=1)).isoformat(),
            }
            rows.append(row)
        price += 0.3
        bench += 0.1
    return rows


def test_feature_and_label_outputs_present() -> None:
    artifact = build_modelable_dataset(_synthetic_rows())
    rows = artifact["rows"]
    sample = rows[20]

    assert sample["floor_d1"] is not None
    assert sample["ceiling_d1"] is not None
    assert sample["floor_w1"] is not None
    assert sample["ceiling_w1"] is not None
    assert sample["floor_q1"] is not None
    assert sample["ceiling_q1"] is not None

    assert sample["floor_time_bucket_d1"] in {"OPEN", "OPEN_PLUS_2H", "OPEN_PLUS_4H", "OPEN_PLUS_6H", "CLOSE"}
    assert sample["ceiling_time_bucket_d1"] in {"OPEN", "OPEN_PLUS_2H", "OPEN_PLUS_4H", "OPEN_PLUS_6H", "CLOSE"}
    assert sample["floor_day_w1"] in {1, 2, 3, 4, 5}
    assert sample["ceiling_day_w1"] in {1, 2, 3, 4, 5}
    assert sample["floor_day_q1"] in {1, 2, 3, 4, 5, 6, 7, 8, 9, 10}
    assert sample["ceiling_day_q1"] in {1, 2, 3, 4, 5, 6, 7, 8, 9, 10}


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
    sample = artifact["rows"][30]
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
    ]:
        assert col in sample
