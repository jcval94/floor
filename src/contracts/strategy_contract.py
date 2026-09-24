from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_STRATEGY_CONTRACTS_PATH = Path("config/strategy_contracts.json")


def load_strategy_contracts(
    path: Path = DEFAULT_STRATEGY_CONTRACTS_PATH,
) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if int(payload.get("schema_version") or 0) != 1:
        raise ValueError("strategy contract registry schema mismatch")
    strategies = payload.get("strategies")
    if not isinstance(strategies, dict):
        raise ValueError("strategy contract registry missing strategies")
    return payload


def strategy_contract(
    strategy_id: str,
    *,
    path: Path = DEFAULT_STRATEGY_CONTRACTS_PATH,
) -> dict[str, Any]:
    contracts = load_strategy_contracts(path)["strategies"]
    contract = contracts.get(strategy_id)
    if not isinstance(contract, dict):
        raise ValueError(f"strategy has no registered semantic contract: {strategy_id}")
    return contract


def validate_strategy_registry(
    strategy_ids: list[str] | tuple[str, ...] | set[str],
    *,
    path: Path = DEFAULT_STRATEGY_CONTRACTS_PATH,
) -> None:
    contracts = load_strategy_contracts(path)["strategies"]
    registry_contracts = {
        strategy_id
        for strategy_id, contract in contracts.items()
        if isinstance(contract, dict)
        and str(contract.get("registry_scope") or "strategy_registry")
        == "strategy_registry"
    }
    missing = sorted(set(strategy_ids) - registry_contracts)
    stale = sorted(registry_contracts - set(strategy_ids))
    if missing:
        raise ValueError(
            "registered strategies missing semantic contract: " + ",".join(missing)
        )
    if stale:
        raise ValueError(
            "strategy contracts reference unregistered strategies: " + ",".join(stale)
        )


def validate_league_strategy_members(
    strategy_ids: list[str] | tuple[str, ...] | set[str],
    *,
    path: Path = DEFAULT_STRATEGY_CONTRACTS_PATH,
) -> None:
    contracts = load_strategy_contracts(path)["strategies"]
    missing = sorted(set(strategy_ids) - set(contracts))
    if missing:
        raise ValueError(
            "Strategy League members missing semantic contract: " + ",".join(missing)
        )
