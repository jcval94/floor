from __future__ import annotations

from backtest.cost_model import CostModel, CostModelConfig
from backtest.execution_simulator import ExecutionConfig, ExecutionSimulator
from backtest.metrics import compute_metrics
from backtest.portfolio_engine import PortfolioEngine
from contracts.trading import (
    round_trip_cost_bps_from_contract,
    strategy_cost_contract,
)


def _cost_model_fields(raw: dict) -> dict[str, float]:
    commission = raw.get("commission_bps")
    if commission is None:
        commission = float(raw.get("broker_commission_bps", 0.0)) + float(
            raw.get("platform_fee_bps_per_side", 0.0)
        )
    return {
        "commission_bps": float(commission or 0.0),
        "slippage_bps": float(raw.get("slippage_bps", 0.0) or 0.0),
        "sell_fee_bps": float(raw.get("sell_fee_bps", 0.0) or 0.0),
        "min_commission": float(raw.get("min_commission", 0.0) or 0.0),
    }


def _resolve_backtest_cost_contract(config: dict) -> tuple[dict[str, float], dict]:
    """Resolve costs fail-closed for evidence-grade backtests.

    Omitted cost_profile means canonical.  A caller that wants alternative
    frictions must opt in explicitly with cost_profile=custom/stress; those
    results remain useful research, but are marked ineligible as canonical
    evidence.
    """

    canonical_raw = strategy_cost_contract()
    canonical = _cost_model_fields(canonical_raw)
    provided_raw = config.get("costs")
    provided = (
        _cost_model_fields(dict(provided_raw))
        if isinstance(provided_raw, dict)
        else None
    )
    profile = str(config.get("cost_profile") or "canonical").lower()

    if profile == "canonical":
        if provided is not None:
            for field, expected in canonical.items():
                if abs(float(provided[field]) - float(expected)) > 1e-12:
                    raise ValueError(
                        "Backtest cost contract drift: non-canonical costs require "
                        "explicit cost_profile='custom' or cost_profile='stress'. "
                        f"field={field} expected={expected} actual={provided[field]}"
                    )
        resolved = canonical
        evidence_eligible = True
    elif profile in {"custom", "stress"}:
        if provided is None:
            raise ValueError(
                f"cost_profile={profile!r} requires an explicit config['costs'] mapping"
            )
        resolved = provided
        evidence_eligible = False
    else:
        raise ValueError(
            "Unsupported cost_profile. Use 'canonical', 'custom', or 'stress'."
        )

    round_trip = round_trip_cost_bps_from_contract(resolved)
    metadata = {
        "profile": profile,
        "canonical": profile == "canonical",
        "canonical_evidence_eligible": evidence_eligible,
        "round_trip_cost_bps": round_trip,
        "costs": dict(resolved),
    }
    return resolved, metadata


def run_portfolio_backtest(
    market_data: list[dict],
    strategy_targets: dict[str, dict[str, dict[str, float]]],
    config: dict,
) -> dict:
    costs, cost_contract = _resolve_backtest_cost_contract(config)
    cost_model = CostModel(CostModelConfig(**costs))
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
    result["cost_contract"] = cost_contract
    result["metrics"] = compute_metrics(
        result,
        horizons=config.get("horizons", [5, 21, 63]),
    )
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
    evidence_eligible = bool(
        champion["cost_contract"]["canonical_evidence_eligible"]
        and challenger["cost_contract"]["canonical_evidence_eligible"]
    )
    return {
        "winner": winner,
        "champion": champion,
        "challenger": challenger,
        "delta_equity": challenger_total - champion_total,
        "canonical_evidence_eligible": evidence_eligible,
    }
