from __future__ import annotations

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
