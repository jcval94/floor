from __future__ import annotations

import pytest

from replay.walk_forward_oos import _aggregate_continuous_folds


def _final_row(strategy: str, nav: float, costs: float, trades: int) -> dict:
    return {
        "strategy": strategy,
        "nav": nav,
        "return": nav / 10_000.0 - 1.0,
        "sharpe": 1.0,
        "max_drawdown": -0.02,
        "trades": trades,
        "costs_paid": costs,
        "suppressed_rebalances": 3,
        "equity_curve": [
            {"session": "2026-03-02", "nav": 10_000.0},
            {"session": "2026-04-01", "nav": 10_100.0},
            {"session": "2026-05-01", "nav": nav},
        ],
    }


def test_continuous_fold_aggregation_uses_final_account_not_reset_fold_sums() -> None:
    final = _final_row("benchmark_spy", 10_300.0, 31.0, 1)
    folds = [
        {
            "tournament": {
                "fold_rows": [
                    {
                        "strategy": "benchmark_spy",
                        "return": 0.01,
                        "costs_paid": 31.0,
                        "trades": 1,
                    }
                ],
                "leaderboard": {"rows": [_final_row("benchmark_spy", 10_100.0, 31.0, 1)]},
            }
        },
        {
            "tournament": {
                "fold_rows": [
                    {
                        "strategy": "benchmark_spy",
                        "return": pytest.approx(10_300.0 / 10_100.0 - 1.0),
                        "costs_paid": 0.0,
                        "trades": 0,
                    }
                ],
                "leaderboard": {"rows": [final]},
            }
        },
    ]
    # Replace pytest.approx marker with the actual value used by production code.
    folds[1]["tournament"]["fold_rows"][0]["return"] = 10_300.0 / 10_100.0 - 1.0

    rows = _aggregate_continuous_folds(folds, 10_000.0)
    row = rows[0]

    assert row["nav"] == pytest.approx(10_300.0)
    assert row["return"] == pytest.approx(0.03)
    assert row["trades"] == 1
    assert row["costs_paid_continuous"] == pytest.approx(31.0)
    assert row["costs_paid_per_10k_fold_sum"] == pytest.approx(31.0)
    assert row["positive_folds"] == 2
    assert row["suppressed_rebalances"] == 3
