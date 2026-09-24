from __future__ import annotations

import pytest

from backtest.run_backtest import compare_champion_challenger, run_portfolio_backtest, run_strategy_backtest


def _market_data() -> list[dict]:
    return [
        {"date": "2026-01-01", "ticker": "AAA", "open": 100, "high": 102, "low": 99, "close": 101, "volume": 100_000},
        {"date": "2026-01-01", "ticker": "BBB", "open": 50, "high": 51, "low": 49, "close": 50, "volume": 80_000},
        {"date": "2026-01-02", "ticker": "AAA", "open": 101, "high": 104, "low": 100, "close": 103, "volume": 100_000},
        {"date": "2026-01-02", "ticker": "BBB", "open": 50, "high": 50, "low": 47, "close": 48, "volume": 80_000},
        {"date": "2026-01-03", "ticker": "AAA", "open": 103, "high": 105, "low": 101, "close": 102, "volume": 100_000},
        {"date": "2026-01-03", "ticker": "BBB", "open": 48, "high": 49, "low": 46, "close": 47, "volume": 80_000},
    ]


def _config() -> dict:
    return {
        "cost_profile": "custom",
        "costs": {
            "commission_bps": 2.0,
            "slippage_bps": 1.0,
            "sell_fee_bps": 3.0,
            "min_commission": 0.0,
        },
        "execution": {
            "max_participation_rate": 0.2,
            "price_reference": "ohlc4",
        },
        "portfolio": {
            "initial_cash": 100_000,
            "max_gross_exposure": 1.0,
            "allow_short": False,
            "strategy_weights": {"s1": 0.6, "s2": 0.4},
        },
        "horizons": [1, 2],
    }


def test_strategy_backtest_includes_required_metrics() -> None:
    data = _market_data()
    strategy_target = {
        "2026-01-01": {"AAA": 0.5},
        "2026-01-02": {"AAA": 0.5},
        "2026-01-03": {"AAA": 0.0},
    }

    result = run_strategy_backtest(data, "s1", strategy_target, _config())
    summary = result["metrics"]["summary"]

    required = {
        "cagr",
        "sharpe",
        "sortino",
        "max_drawdown",
        "hit_rate",
        "profit_factor",
        "average_win",
        "average_loss",
        "turnover",
        "capacity_warning",
        "contribution_by_strategy",
        "contribution_by_ticker",
    }
    assert required.issubset(summary)
    assert result["equity_curve"]
    assert isinstance(summary["contribution_by_strategy"], dict)


def test_multistrategy_portfolio_and_breakdowns() -> None:
    data = _market_data()
    targets = {
        "s1": {
            "2026-01-01": {"AAA": 0.6},
            "2026-01-02": {"AAA": 0.6},
            "2026-01-03": {"AAA": 0.0},
        },
        "s2": {
            "2026-01-01": {"BBB": 0.5},
            "2026-01-02": {"BBB": 0.5},
            "2026-01-03": {"BBB": 0.0},
        },
    }

    result = run_portfolio_backtest(data, targets, _config())

    assert result["turnover"] >= 0
    assert "AAA" in result["metrics"]["by_ticker"]
    assert "BBB" in result["metrics"]["by_ticker"]
    assert "return_1" in result["metrics"]["by_horizon"]
    assert set(result["metrics"]["summary"]["contribution_by_strategy"]) == {"s1", "s2"}


def test_champion_challenger_comparison() -> None:
    data = _market_data()
    champion = {
        "champion_s": {
            "2026-01-01": {"AAA": 0.2},
            "2026-01-02": {"AAA": 0.2},
            "2026-01-03": {"AAA": 0.0},
        }
    }
    challenger = {
        "challenger_s": {
            "2026-01-01": {"AAA": 0.7},
            "2026-01-02": {"AAA": 0.7},
            "2026-01-03": {"AAA": 0.0},
        }
    }

    cmp_result = compare_champion_challenger(data, champion, challenger, _config())

    assert cmp_result["winner"] in {"champion", "challenger"}
    assert "delta_equity" in cmp_result
    assert cmp_result["champion"]["equity_curve"]
    assert cmp_result["challenger"]["equity_curve"]


def test_generic_backtest_requires_explicit_opt_in_for_noncanonical_costs() -> None:
    config = _config()
    config.pop("cost_profile")

    with pytest.raises(ValueError, match="non-canonical costs require"):
        run_strategy_backtest(
            _market_data(),
            "s1",
            {
                "2026-01-01": {"AAA": 0.5},
                "2026-01-02": {"AAA": 0.5},
                "2026-01-03": {"AAA": 0.0},
            },
            config,
        )


def test_canonical_backtest_contract_is_61_bps_and_evidence_eligible() -> None:
    config = _config()
    config["cost_profile"] = "canonical"
    config["costs"] = {
        "commission_bps": 26.0,
        "slippage_bps": 3.0,
        "sell_fee_bps": 3.0,
        "min_commission": 0.0,
    }

    result = run_strategy_backtest(
        _market_data(),
        "s1",
        {
            "2026-01-01": {"AAA": 0.5},
            "2026-01-02": {"AAA": 0.5},
            "2026-01-03": {"AAA": 0.0},
        },
        config,
    )

    assert result["cost_contract"]["profile"] == "canonical"
    assert result["cost_contract"]["round_trip_cost_bps"] == pytest.approx(61.0)
    assert result["cost_contract"]["canonical_evidence_eligible"] is True


def test_backtest_auxiliary_pnl_is_net_of_costs_and_reconciles_to_equity() -> None:
    result = run_strategy_backtest(
        _market_data(),
        "s1",
        {
            "2026-01-01": {"AAA": 0.5},
            "2026-01-02": {"AAA": 0.5},
            "2026-01-03": {"AAA": 0.0},
        },
        _config(),
    )

    summary = result["metrics"]["summary"]
    final_pnl = result["equity_curve"][-1]["equity"] - _config()["portfolio"]["initial_cash"]

    assert result["total_costs"] > 0
    assert result["realized_pnl"] < result["gross_realized_pnl"]
    assert result["pnl_reconciliation_error"] == pytest.approx(0.0, abs=1e-8)
    assert summary["pnl_reconciliation_error"] == pytest.approx(0.0, abs=1e-8)
    assert sum(result["ticker_pnl"].values()) == pytest.approx(final_pnl, abs=1e-8)
    assert sum(result["strategy_cost_allocation"].values()) == pytest.approx(
        result["total_costs"],
        abs=1e-8,
    )
    assert summary["contribution_by_strategy"]["s1"] < (
        summary["contribution_by_strategy_gross"]["s1"]
    )
