from __future__ import annotations

from dataclasses import dataclass
from math import floor
from pathlib import Path
from typing import Any, Iterable

from strategies.run_strategies import load_simple_yaml


@dataclass(frozen=True)
class RiskPolicy:
    nav_usd: float
    max_position_notional_usd: float
    max_gross_exposure_usd: float
    max_single_name_weight: float
    max_sector_weight: float
    daily_loss_limit_bps: float
    kill_switch_enabled: bool


@dataclass(frozen=True)
class RiskApprovalResult:
    orders: list[dict[str, Any]]
    rejected: list[dict[str, Any]]
    policy: RiskPolicy


def load_risk_policy(
    risk_path: Path = Path("config/risk.yaml"),
    strategies_path: Path = Path("config/strategies.yaml"),
) -> RiskPolicy:
    risk_cfg = load_simple_yaml(risk_path).get("risk", {})
    strategy_cfg = load_simple_yaml(strategies_path)
    portfolio_cfg = strategy_cfg.get("portfolio", {})

    policy = RiskPolicy(
        nav_usd=float(portfolio_cfg.get("nav_usd", 0.0)),
        max_position_notional_usd=float(risk_cfg.get("max_position_notional_usd", 0.0)),
        max_gross_exposure_usd=float(risk_cfg.get("max_gross_exposure_usd", 0.0)),
        max_single_name_weight=float(risk_cfg.get("max_single_name_weight", 0.0)),
        max_sector_weight=float(risk_cfg.get("max_sector_weight", 0.0)),
        daily_loss_limit_bps=float(risk_cfg.get("daily_loss_limit_bps", 0.0)),
        kill_switch_enabled=bool(risk_cfg.get("kill_switch", {}).get("enabled", True)),
    )
    _validate_policy(policy)
    return policy


def approve_signal_batch(
    signals: Iterable[Any],
    market_rows: Iterable[dict[str, Any]],
    *,
    policy: RiskPolicy,
    live_trading_enabled: bool = False,
    market_data_fresh: bool = True,
    realized_pnl_usd: float = 0.0,
    existing_gross_notional_usd: float = 0.0,
    existing_symbol_notional_usd: dict[str, float] | None = None,
    existing_sector_notional_usd: dict[str, float] | None = None,
    existing_symbol_quantity: dict[str, int] | None = None,
) -> RiskApprovalResult:
    """Convert model signals into executor-compatible order intents after risk approval.

    `config/risk.yaml` is authoritative. Strategy-level position limits may be more
    conservative, but they can never enlarge these global caps. Exposure-increasing
    orders obey every cap. Exposure-reducing orders are allowed to close an existing
    position even when a capacity or daily-loss limit is already breached.
    """

    if live_trading_enabled:
        raise RuntimeError("LIVE execution is blocked: audited broker gateway is not implemented")

    rows_by_symbol = {
        str(row.get("symbol") or "").strip().upper(): dict(row)
        for row in market_rows
        if str(row.get("symbol") or "").strip()
    }
    existing_symbol = {str(k).upper(): float(v) for k, v in (existing_symbol_notional_usd or {}).items()}
    existing_sector = {str(k): float(v) for k, v in (existing_sector_notional_usd or {}).items()}
    existing_qty = {str(k).upper(): int(v) for k, v in (existing_symbol_quantity or {}).items()}

    normalized = [_normalize_signal(signal) for signal in signals]
    normalized = [signal for signal in normalized if signal["action"] in {"BUY", "SELL"}]
    rejected: list[dict[str, Any]] = []

    # Stale prices make both sizing and simulated fills unsafe, including exits.
    if policy.kill_switch_enabled and not market_data_fresh:
        return RiskApprovalResult(
            orders=[],
            rejected=[{**signal, "reason": "kill_switch: stale_data"} for signal in normalized],
            policy=policy,
        )

    selected: list[dict[str, Any]] = []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for signal in normalized:
        grouped.setdefault(signal["symbol"], []).append(signal)

    horizon_rank = {"d1": 0, "w1": 1, "q1": 2, "m3": 3}
    for symbol in sorted(grouped):
        items = grouped[symbol]
        sides = {item["action"] for item in items}
        if len(sides) > 1:
            position_qty = int(existing_qty.get(symbol, 0))
            reducing_side = "SELL" if position_qty > 0 else "BUY" if position_qty < 0 else None
            reducing_items = [item for item in items if reducing_side and item["action"] == reducing_side]
            if not reducing_items:
                rejected.extend({**item, "reason": "cross_horizon_side_conflict"} for item in items)
                continue
            winner = sorted(
                reducing_items,
                key=lambda item: (-float(item["confidence"]), horizon_rank.get(str(item["horizon"]), 99)),
            )[0]
            selected.append(winner)
            rejected.extend(
                {**item, "reason": "cross_horizon_conflict_prefer_decrease"}
                for item in items
                if item is not winner
            )
            continue
        winner = sorted(
            items,
            key=lambda item: (-float(item["confidence"]), horizon_rank.get(str(item["horizon"]), 99)),
        )[0]
        selected.append(winner)
        rejected.extend({**item, "reason": "lower_priority_same_symbol"} for item in items if item is not winner)

    orders: list[dict[str, Any]] = []
    gross = max(0.0, float(existing_gross_notional_usd))
    projected_symbol = dict(existing_symbol)
    projected_sector = dict(existing_sector)
    projected_qty = dict(existing_qty)
    daily_loss_limit = policy.nav_usd * policy.daily_loss_limit_bps / 10_000.0
    daily_loss_hit = policy.kill_switch_enabled and realized_pnl_usd <= -daily_loss_limit

    for signal in sorted(selected, key=lambda item: (-float(item["confidence"]), item["symbol"])):
        symbol = signal["symbol"]
        row = rows_by_symbol.get(symbol)
        if row is None:
            rejected.append({**signal, "reason": "missing_market_row"})
            continue
        risk_price = _risk_reference_price(row)
        if risk_price is None:
            rejected.append({**signal, "reason": "invalid_market_price"})
            continue

        sector = str(row.get("sector") or "UNKNOWN")
        current_quantity = int(projected_qty.get(symbol, 0))
        current_symbol = max(0.0, projected_symbol.get(symbol, 0.0))
        current_sector = max(0.0, projected_sector.get(sector, 0.0))
        reducing = (current_quantity > 0 and signal["action"] == "SELL") or (
            current_quantity < 0 and signal["action"] == "BUY"
        )

        if reducing:
            quantity = abs(current_quantity)
            approved_notional = quantity * risk_price
            order = _build_order(
                signal,
                sector=sector,
                price=risk_price,
                quantity=quantity,
                approved_notional=approved_notional,
                policy=policy,
                risk_action="decrease_exposure",
            )
            orders.append(order)
            gross = max(0.0, gross - current_symbol)
            projected_symbol[symbol] = 0.0
            projected_sector[sector] = max(0.0, current_sector - current_symbol)
            projected_qty[symbol] = 0
            continue

        if daily_loss_hit:
            rejected.append({**signal, "reason": "kill_switch: daily_loss_limit"})
            continue

        global_position_cap = min(
            policy.max_position_notional_usd,
            policy.nav_usd * policy.max_single_name_weight,
        )
        symbol_room = max(0.0, global_position_cap - current_symbol)
        gross_room = max(0.0, policy.max_gross_exposure_usd - gross)
        sector_room = max(0.0, policy.nav_usd * policy.max_sector_weight - current_sector)
        allowed_notional = min(symbol_room, gross_room, sector_room)
        quantity = floor(allowed_notional / risk_price)

        if quantity <= 0:
            rejected.append({**signal, "reason": "risk_capacity_exhausted"})
            continue

        approved_notional = quantity * risk_price
        order = _build_order(
            signal,
            sector=sector,
            price=risk_price,
            quantity=quantity,
            approved_notional=approved_notional,
            policy=policy,
            risk_action="increase_exposure",
        )
        orders.append(order)
        gross += approved_notional
        projected_symbol[symbol] = current_symbol + approved_notional
        projected_sector[sector] = current_sector + approved_notional
        signed_delta = quantity if signal["action"] == "BUY" else -quantity
        projected_qty[symbol] = current_quantity + signed_delta

    return RiskApprovalResult(orders=orders, rejected=rejected, policy=policy)


def _build_order(
    signal: dict[str, Any],
    *,
    sector: str,
    price: float,
    quantity: int,
    approved_notional: float,
    policy: RiskPolicy,
    risk_action: str,
) -> dict[str, Any]:
    return {
        "strategy_id": "canonical_model_signal",
        "symbol": signal["symbol"],
        "side": signal["action"],
        "quantity": quantity,
        "metadata": {
            "horizon": signal["horizon"],
            "confidence": signal["confidence"],
            "rationale": signal["rationale"],
            "sector": sector,
            "risk_reference_price": price,
            "approved_notional_usd": round(approved_notional, 2),
            "risk_action": risk_action,
            "risk_policy": {
                "max_position_notional_usd": policy.max_position_notional_usd,
                "max_gross_exposure_usd": policy.max_gross_exposure_usd,
                "max_single_name_weight": policy.max_single_name_weight,
                "max_sector_weight": policy.max_sector_weight,
            },
        },
    }


def _normalize_signal(signal: Any) -> dict[str, Any]:
    if isinstance(signal, dict):
        raw = signal
    else:
        raw = {
            "symbol": getattr(signal, "symbol", ""),
            "action": getattr(signal, "action", "HOLD"),
            "horizon": getattr(signal, "horizon", ""),
            "confidence": getattr(signal, "confidence", 0.0),
            "rationale": getattr(signal, "rationale", ""),
        }
    return {
        "symbol": str(raw.get("symbol") or "").strip().upper(),
        "action": str(raw.get("action") or "HOLD").strip().upper(),
        "horizon": str(raw.get("horizon") or ""),
        "confidence": float(raw.get("confidence") or 0.0),
        "rationale": str(raw.get("rationale") or ""),
    }


def _validate_policy(policy: RiskPolicy) -> None:
    if policy.nav_usd <= 0:
        raise ValueError("risk policy nav_usd must be > 0")
    if policy.max_position_notional_usd <= 0 or policy.max_gross_exposure_usd <= 0:
        raise ValueError("risk notional limits must be > 0")
    if not 0 < policy.max_single_name_weight <= 1:
        raise ValueError("max_single_name_weight must be in (0, 1]")
    if not 0 < policy.max_sector_weight <= 1:
        raise ValueError("max_sector_weight must be in (0, 1]")
    if policy.daily_loss_limit_bps <= 0:
        raise ValueError("daily_loss_limit_bps must be > 0")


def _risk_reference_price(row: dict[str, Any]) -> float | None:
    """Use a conservative upper price so OHLC4 fills cannot exceed risk notional."""

    prices = [_positive_float(row.get(key)) for key in ("open", "high", "low", "close")]
    valid = [price for price in prices if price is not None]
    return max(valid) if valid else None


def _positive_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
