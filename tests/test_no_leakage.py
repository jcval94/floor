from __future__ import annotations

from features.labels import build_labels


def test_labels_depend_only_on_future_days_not_future_intraday_of_same_day() -> None:
    rows = [
        {"symbol": "AAA", "timestamp": "2026-03-10T10:00:00", "open": 100, "high": 101, "low": 99, "close": 100},
        {"symbol": "AAA", "timestamp": "2026-03-10T15:00:00", "open": 100, "high": 102, "low": 98, "close": 101},
        {"symbol": "AAA", "timestamp": "2026-03-11T10:00:00", "open": 101, "high": 103, "low": 100, "close": 102},
        {"symbol": "AAA", "timestamp": "2026-03-11T15:00:00", "open": 102, "high": 104, "low": 101, "close": 103},
    ]

    labeled = build_labels(rows)
    first = labeled[0]

    # d1 labels for 2026-03-10 should come from 2026-03-11 only.
    assert first["floor_d1"] == 100
    assert first["ceiling_d1"] == 104


def test_no_forward_columns_in_features_contract_example() -> None:
    feature_row = {
        "timestamp": "2026-03-12T10:00:00Z",
        "feature_close_lag_1": 99.0,
        "feature_close_lag_2": 98.0,
    }
    assert "future_close" not in feature_row
    assert "label_h1" not in feature_row
