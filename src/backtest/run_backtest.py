from __future__ import annotations

from backtest.cost_model import CostModel, CostModelConfig
from backtest.execution_simulator import ExecutionConfig, ExecutionSimulator
from backtest.metrics import compute_metrics
from backtest.portfolio_engine import PortfolioEngine


def run_portfolio_backtest(
    market_data: list[dict],
    strategy_targets: dict[str, dict[str, dict[str, float]]],
    config: dict,
) -> dict:
    cost_model = CostModel(CostModelConfig(**config["costs"]))
    simulator = ExecutionSimulator(ExecutionConfig(**config["execution"]))
    engine = PortfolioEngine(
        cost_model=cost_model,
        execution_simulator=simulator,
        initial_cash=float(config["portfolio"]["initial_cash"]),
        max_gross_exposure=float(config["portfolio"].get("max_gross_exposure", 1.0)),
        allow_short=bool(config["portfolio"].get("allow_short", False)),
        strategy_weights=config["portfolio"].get("strategy_weights", {}),
    )
    result = engine.run(market_data=market_data, strategy_targets=strategy_targets)
    result["metrics"] = compute_metrics(result, horizons=config.get("horizons", [5, 21, 63]))
    return result


def run_strategy_backtest(
    market_data: list[dict],
    strategy_id: str,
    strategy_target: dict[str, dict[str, float]],
    config: dict,
) -> dict:
    return run_portfolio_backtest(market_data, {strategy_id: strategy_target}, config)


def compare_champion_challenger(
    market_data: list[dict],
    champion_targets: dict[str, dict[str, dict[str, float]]],
    challenger_targets: dict[str, dict[str, dict[str, float]]],
    config: dict,
) -> dict:
    champion = run_portfolio_backtest(market_data, champion_targets, config)
    challenger = run_portfolio_backtest(market_data, challenger_targets, config)

    champion_total = champion["equity_curve"][-1]["equity"]
    challenger_total = challenger["equity_curve"][-1]["equity"]

    winner = "champion" if champion_total >= challenger_total else "challenger"
    return {
        "winner": winner,
        "champion": champion,
        "challenger": challenger,
        "delta_equity": challenger_total - champion_total,
    }
