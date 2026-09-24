from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from contracts.config_io import load_simple_yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_COSTS_PATH = REPO_ROOT / "config" / "costs.yaml"
DEFAULT_RISK_PATH = REPO_ROOT / "config" / "risk.yaml"
DEFAULT_STRATEGIES_PATH = REPO_ROOT / "config" / "strategies.yaml"


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def round_trip_cost_bps_from_contract(costs: dict[str, Any]) -> float:
    """Return the exact all-in round-trip friction for one buy then sell.

    The sell fee is charged once; broker/platform/slippage are charged on both
    sides. Keeping this formula in the trading contract prevents research,
    strategy gates and execution from drifting (for example 58 vs 61 bps).
    """

    broker = _float(
        costs.get("broker_commission_bps"),
        _float(costs.get("commission_bps"), 0.0)
        - _float(costs.get("platform_fee_bps_per_side"), 0.0),
    )
    platform = _float(costs.get("platform_fee_bps_per_side"), 0.0)
    slippage = _float(costs.get("slippage_bps"), 0.0)
    sell_fee = _float(costs.get("sell_fee_bps"), 0.0)
    return 2.0 * (broker + platform + slippage) + sell_fee


def load_trading_cost_contract(
    costs_path: Path = DEFAULT_COSTS_PATH,
) -> dict[str, float]:
    raw = load_simple_yaml(costs_path).get("cost_model", {})
    broker = _float(
        raw.get("broker_commission_bps"),
        _float(raw.get("commission_bps"), 0.0),
    )
    platform = _float(raw.get("platform_fee_bps_per_side"), 0.0)
    strategy_slippage = _float(
        raw.get("strategy_slippage_bps"),
        _float(raw.get("slippage_bps_open"), _float(raw.get("slippage_bps"), 0.0)),
    )
    paper_slippage = _float(
        raw.get("paper_slippage_bps"),
        strategy_slippage,
    )
    shadow_slippage = _float(
        raw.get("shadow_slippage_bps"),
        strategy_slippage,
    )
    return {
        "broker_commission_bps": broker,
        "platform_fee_bps_per_side": platform,
        "strategy_slippage_bps": strategy_slippage,
        "paper_slippage_bps": paper_slippage,
        "shadow_slippage_bps": shadow_slippage,
        "strategy_sell_fee_bps": _float(raw.get("strategy_sell_fee_bps"), 0.0),
        "paper_sell_fee_bps": _float(raw.get("paper_sell_fee_bps"), 0.0),
        "shadow_sell_fee_bps": _float(raw.get("shadow_sell_fee_bps"), 0.0),
    }


def strategy_cost_contract(
    costs_path: Path = DEFAULT_COSTS_PATH,
) -> dict[str, float]:
    costs = load_trading_cost_contract(costs_path)
    return {
        "broker_commission_bps": costs["broker_commission_bps"],
        "platform_fee_bps_per_side": costs["platform_fee_bps_per_side"],
        "slippage_bps": costs["strategy_slippage_bps"],
        "sell_fee_bps": costs["strategy_sell_fee_bps"],
        "commission_bps": (
            costs["broker_commission_bps"]
            + costs["platform_fee_bps_per_side"]
        ),
    }


def paper_cost_contract(
    costs_path: Path = DEFAULT_COSTS_PATH,
) -> dict[str, float]:
    costs = load_trading_cost_contract(costs_path)
    return {
        "commission_bps": (
            costs["broker_commission_bps"]
            + costs["platform_fee_bps_per_side"]
        ),
        "slippage_bps": costs["paper_slippage_bps"],
        "sell_fee_bps": costs["paper_sell_fee_bps"],
        "min_commission": 0.0,
    }


def shadow_execution_contract(
    costs_path: Path = DEFAULT_COSTS_PATH,
) -> dict[str, float]:
    costs = load_trading_cost_contract(costs_path)
    return {
        "commission_bps": (
            costs["broker_commission_bps"]
            + costs["platform_fee_bps_per_side"]
        ),
        "broker_commission_bps": costs["broker_commission_bps"],
        "platform_fee_bps_per_side": costs["platform_fee_bps_per_side"],
        "slippage_bps": costs["shadow_slippage_bps"],
        "sell_fee_bps": costs["shadow_sell_fee_bps"],
    }


def load_operational_risk_contract(
    risk_path: Path = DEFAULT_RISK_PATH,
) -> dict[str, Any]:
    risk = load_simple_yaml(risk_path).get("risk", {})
    if not isinstance(risk, dict):
        raise ValueError("config/risk.yaml must contain a risk mapping")
    return risk


def load_strategy_runtime_config(
    strategies_path: Path = DEFAULT_STRATEGIES_PATH,
    *,
    costs_path: Path = DEFAULT_COSTS_PATH,
    risk_path: Path = DEFAULT_RISK_PATH,
) -> dict:
    """Hydrate research/PAPER strategy config from authoritative cost/risk files.

    Strategy-local limits may be more conservative, never looser, than the
    operational risk contract. Strategy League intentionally does not call this
    function because it is a frozen experiment with its own explicitly scoped
    allocation limits.
    """

    cfg = deepcopy(load_simple_yaml(strategies_path))
    cfg["costs"] = strategy_cost_contract(costs_path)

    risk = load_operational_risk_contract(risk_path)
    portfolio = cfg.setdefault("portfolio", {})
    nav = max(_float(portfolio.get("nav_usd")), 0.0)
    max_single = _float(risk.get("max_single_name_weight"), 1.0)
    max_sector = _float(risk.get("max_sector_weight"), 1.0)
    max_position_notional = _float(risk.get("max_position_notional_usd"), 0.0)
    max_gross_notional = _float(risk.get("max_gross_exposure_usd"), 0.0)

    portfolio["max_position_pct_nav"] = min(
        _float(portfolio.get("max_position_pct_nav"), 1.0),
        max_single,
    )
    portfolio["max_sector_exposure_pct_nav"] = min(
        _float(portfolio.get("max_sector_exposure_pct_nav"), 1.0),
        max_sector,
    )
    if nav > 0 and max_gross_notional > 0:
        portfolio["max_gross_exposure_pct_nav"] = min(
            _float(portfolio.get("max_gross_exposure_pct_nav"), 1.0),
            max_gross_notional / nav,
        )

    strategies = cfg.get("strategies", {})
    if isinstance(strategies, dict):
        for strategy in strategies.values():
            if not isinstance(strategy, dict):
                continue
            sizing = strategy.setdefault("position_sizing", {})
            sizing["max_weight_pct_nav"] = min(
                _float(sizing.get("max_weight_pct_nav"), 1.0),
                max_single,
            )
            current_notional = _float(sizing.get("max_notional_usd"), 0.0)
            if max_position_notional > 0:
                sizing["max_notional_usd"] = (
                    min(current_notional, max_position_notional)
                    if current_notional > 0
                    else max_position_notional
                )

    cfg["contract_meta"] = {
        "cost_authority": str(costs_path),
        "risk_authority": str(risk_path),
        "risk_scope": "operational",
    }
    return cfg


def validate_shadow_execution_contract(
    execution: dict[str, Any],
    *,
    costs_path: Path = DEFAULT_COSTS_PATH,
    tolerance: float = 1e-9,
) -> None:
    expected = shadow_execution_contract(costs_path)
    for key, value in expected.items():
        actual = _float(execution.get(key), float("nan"))
        if abs(actual - value) > tolerance:
            raise ValueError(
                "Strategy League execution contract drift: "
                f"{key} expected={value} actual={execution.get(key)}"
            )
