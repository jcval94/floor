from datetime import date

import pytest

from replay.walk_forward_oos import (\n    _aggregate_continuous_folds,\n    _fold_sessions,\n    _training_cutoff_audit,\n)


def test_training_cutoff_requires_observation_and_m3_target_before_fold() -> None:
    payload = {
        "rows": [
            {"timestamp": "2025-12-01T00:00:00+00:00", "target_end_date_m3": "2026-02-20"},
            {"timestamp": "2026-01-02T00:00:00+00:00", "target_end_date_m3": "2026-02-27"},
        ]
    }
    audit = _training_cutoff_audit(payload, date(2026, 3, 2))
    assert audit["training_observation_before_fold"] is True
    assert audit["training_target_maturity_before_fold"] is True


def test_training_cutoff_rejects_unmatured_target() -> None:
    payload = {
        "rows": [
            {"timestamp": "2026-01-02T00:00:00+00:00", "target_end_date_m3": "2026-03-02"},
        ]
    }
    with pytest.raises(RuntimeError, match="training purge failed"):
        _training_cutoff_audit(payload, date(2026, 3, 2))


def test_fold_sessions_merges_tiny_tail() -> None:
    sessions = [date(2026, 1, day) for day in range(1, 13)]
    folds = _fold_sessions(sessions, 5)
    assert [len(fold) for fold in folds] == [5, 7]


def test_aggregate_continuous_folds_uses_final_ledger_and_fold_returns() -> None:
    folds = [
        {
            "tournament": {
                "fold_rows": [
                    {
                        "strategy": "capital_allocation_challenger",
                        "return": 0.10,
                        "trades": 1,
                        "costs_paid": 10.0,
                    },
                    {
                        "strategy": "benchmark_spy",
                        "return": 0.05,
                        "trades": 1,
                        "costs_paid": 2.0,
                    },
                ],
                "leaderboard": {
                    "rows": [
                        {
                            "strategy": "capital_allocation_challenger",
                            "nav": 11000.0,
                            "return": 0.10,
                            "sharpe": 1.0,
                            "max_drawdown": -0.01,
                            "trades": 1,
                            "costs_paid": 10.0,
                            "suppressed_rebalances": 0,
                            "equity_curve": [
                                {"session": "2026-01-01", "nav": 10000.0},
                                {"session": "2026-01-02", "nav": 11000.0},
                            ],
                        },
                        {
                            "strategy": "benchmark_spy",
                            "nav": 10500.0,
                            "return": 0.05,
                            "sharpe": 1.0,
                            "max_drawdown": 0.0,
                            "trades": 1,
                            "costs_paid": 2.0,
                            "suppressed_rebalances": 0,
                            "equity_curve": [
                                {"session": "2026-01-01", "nav": 10000.0},
                                {"session": "2026-01-02", "nav": 10500.0},
                            ],
                        },
                    ]
                },
            }
        },
        {
            "tournament": {
                "fold_rows": [
                    {
                        "strategy": "capital_allocation_challenger",
                        "return": -0.05,
                        "trades": 0,
                        "costs_paid": 0.0,
                    },
                    {
                        "strategy": "benchmark_spy",
                        "return": 0.0,
                        "trades": 0,
                        "costs_paid": 0.0,
                    },
                ],
                "leaderboard": {
                    "rows": [
                        {
                            "strategy": "capital_allocation_challenger",
                            "nav": 10450.0,
                            "return": 0.045,
                            "sharpe": 0.8,
                            "max_drawdown": -0.05,
                            "trades": 1,
                            "costs_paid": 10.0,
                            "suppressed_rebalances": 2,
                            "equity_curve": [
                                {"session": "2026-01-01", "nav": 10000.0},
                                {"session": "2026-01-02", "nav": 11000.0},
                                {"session": "2026-02-02", "nav": 10450.0},
                            ],
                        },
                        {
                            "strategy": "benchmark_spy",
                            "nav": 10500.0,
                            "return": 0.05,
                            "sharpe": 0.7,
                            "max_drawdown": 0.0,
                            "trades": 1,
                            "costs_paid": 2.0,
                            "suppressed_rebalances": 0,
                            "equity_curve": [
                                {"session": "2026-01-01", "nav": 10000.0},
                                {"session": "2026-01-02", "nav": 10500.0},
                                {"session": "2026-02-02", "nav": 10500.0},
                            ],
                        },
                    ]
                },
            }
        },
    ]
    rows = _aggregate_continuous_folds(folds, 10000.0)
    challenger = next(
        row for row in rows
        if row["strategy"] == "capital_allocation_challenger"
    )
    assert challenger["nav"] == pytest.approx(10450.0)
    assert challenger["return"] == pytest.approx(0.045)
    assert challenger["trades"] == 1
    assert challenger["costs_paid_continuous"] == pytest.approx(10.0)
    assert challenger["positive_folds"] == 1
    assert challenger["vs_spy"] == pytest.approx(-0.005)
