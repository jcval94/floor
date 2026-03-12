from __future__ import annotations

from math import sqrt
from statistics import mean, pstdev


def _safe_mean(values: list[float]) -> float:
    return 0.0 if not values else mean(values)


def _annualized_return(equity_curve: list[dict], periods_per_year: int = 252) -> float:
    if len(equity_curve) < 2:
        return 0.0
    start = float(equity_curve[0]["equity"])
    end = float(equity_curve[-1]["equity"])
    n = len(equity_curve) - 1
    if start <= 0 or n <= 0:
        return 0.0
    return (end / start) ** (periods_per_year / n) - 1.0


def _sharpe(returns: list[float], periods_per_year: int = 252) -> float:
    if len(returns) < 2:
        return 0.0
    vol = pstdev(returns)
    if vol == 0:
        return 0.0
    return (_safe_mean(returns) / vol) * sqrt(periods_per_year)


def _sortino(returns: list[float], periods_per_year: int = 252) -> float:
    if len(returns) < 2:
        return 0.0
    downside = [r for r in returns if r < 0]
    if not downside:
        return 0.0
    downside_dev = pstdev(downside)
    if downside_dev == 0:
        return 0.0
    return (_safe_mean(returns) / downside_dev) * sqrt(periods_per_year)


def _max_drawdown(equity_curve: list[dict]) -> float:
    if not equity_curve:
        return 0.0
    return min(float(x.get("drawdown", 0.0)) for x in equity_curve)


def _trade_stats(closed_trade_pnls: list[float]) -> dict[str, float]:
    wins = [p for p in closed_trade_pnls if p > 0]
    losses = [p for p in closed_trade_pnls if p < 0]
    total = len(closed_trade_pnls)

    hit_rate = 0.0 if total == 0 else len(wins) / total
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = 0.0 if gross_loss == 0 else gross_profit / gross_loss

    return {
        "hit_rate": hit_rate,
        "profit_factor": profit_factor,
        "average_win": _safe_mean(wins),
        "average_loss": _safe_mean(losses),
    }


def metrics_by_horizon(equity_curve: list[dict], horizons: list[int]) -> dict[str, float]:
    values = [float(r["equity"]) for r in equity_curve]
    out: dict[str, float] = {}
    for h in horizons:
        if h <= 0 or len(values) <= h or values[-h - 1] == 0:
            out[f"return_{h}"] = 0.0
            continue
        out[f"return_{h}"] = values[-1] / values[-h - 1] - 1.0
    return out


def metrics_by_ticker(ticker_pnl: dict[str, float], ticker_turnover: dict[str, float]) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for ticker in sorted(set(ticker_pnl) | set(ticker_turnover)):
        result[ticker] = {
            "pnl": ticker_pnl.get(ticker, 0.0),
            "turnover": ticker_turnover.get(ticker, 0.0),
        }
    return result


def compute_metrics(result: dict, horizons: list[int] | None = None, periods_per_year: int = 252) -> dict:
    horizons = horizons or [5, 21, 63]
    equity_curve = result["equity_curve"]
    returns = [float(x.get("daily_return", 0.0)) for x in equity_curve[1:]]

    summary = {
        "cagr": _annualized_return(equity_curve, periods_per_year),
        "sharpe": _sharpe(returns, periods_per_year),
        "sortino": _sortino(returns, periods_per_year),
        "max_drawdown": _max_drawdown(equity_curve),
        "turnover": float(result.get("turnover", 0.0)),
        "capacity_warning": bool(result.get("capacity_warning", False)),
        "contribution_by_strategy": dict(result.get("strategy_contribution", {})),
        "contribution_by_ticker": dict(result.get("ticker_pnl", {})),
    }
    summary.update(_trade_stats(result.get("closed_trade_pnls", [])))

    return {
        "summary": summary,
        "by_horizon": metrics_by_horizon(equity_curve, horizons),
        "by_ticker": metrics_by_ticker(result.get("ticker_pnl", {}), result.get("ticker_turnover", {})),
    }
