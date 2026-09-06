from __future__ import annotations

from dataclasses import dataclass, field
from math import floor
from typing import Any


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _round_trip_cost_bps(config: dict) -> float:
    costs = config.get("costs", {})
    broker = _float(
        costs.get("broker_commission_bps"),
        _float(costs.get("commission_bps"), 0.0),
    )
    slippage = _float(costs.get("slippage_bps"), 0.0)
    platform = _float(costs.get("platform_fee_bps_per_side"), 0.0)
    return 2.0 * (broker + slippage + platform)


@dataclass
class PortfolioState:
    """Minimal state required to make portfolio-aware sizing decisions.

    The strategy layer proposes a quantity. This state lets the allocator cap
    that proposal using the *current* account rather than a static notional.
    Values default to a fresh all-cash portfolio for backward compatibility.
    """

    equity: float
    cash: float
    gross_exposure: float = 0.0
    open_risk: float = 0.0
    sector_exposure: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, mapping: dict | None, config: dict) -> "PortfolioState":
        portfolio_cfg = config.get("portfolio", {})
        configured_equity = _float(
            portfolio_cfg.get("nav_usd", portfolio_cfg.get("initial_equity_usd", 0.0))
        )
        source = mapping or {}
        equity = max(_float(source.get("equity"), configured_equity), 0.0)
        cash = max(_float(source.get("cash"), equity), 0.0)
        sectors_raw = source.get("sector_exposure", {})
        sectors = (
            {str(key): max(_float(value), 0.0) for key, value in sectors_raw.items()}
            if isinstance(sectors_raw, dict)
            else {}
        )
        return cls(
            equity=equity,
            cash=cash,
            gross_exposure=max(_float(source.get("gross_exposure")), 0.0),
            open_risk=max(_float(source.get("open_risk")), 0.0),
            sector_exposure=sectors,
        )


@dataclass(frozen=True)
class AllocationResult:
    quantity: int
    entry_price: float
    notional_usd: float
    risk_per_share_usd: float
    risk_at_stop_usd: float
    weight_pct_nav: float
    portfolio_heat_after_pct_nav: float
    gross_exposure_after_pct_nav: float
    sector_exposure_after_pct_nav: float
    binding_limit: str
    blocked_reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "quantity": self.quantity,
            "entry_price": round(self.entry_price, 6),
            "notional_usd": round(self.notional_usd, 6),
            "risk_per_share_usd": round(self.risk_per_share_usd, 6),
            "risk_at_stop_usd": round(self.risk_at_stop_usd, 6),
            "weight_pct_nav": round(self.weight_pct_nav, 8),
            "portfolio_heat_after_pct_nav": round(
                self.portfolio_heat_after_pct_nav, 8
            ),
            "gross_exposure_after_pct_nav": round(
                self.gross_exposure_after_pct_nav, 8
            ),
            "sector_exposure_after_pct_nav": round(
                self.sector_exposure_after_pct_nav, 8
            ),
            "binding_limit": self.binding_limit,
            "blocked_reason": self.blocked_reason,
        }


def _qty_cap(name: str, amount: float, entry: float) -> tuple[str, int]:
    if amount == float("inf"):
        return name, 2**31 - 1
    return name, max(0, floor(max(amount, 0.0) / max(entry, 1e-12)))


def cap_order_quantity(
    *,
    proposed_qty: int,
    side: str,
    entry_price: float,
    stop_price: float,
    sector: str,
    strategy_cfg: dict,
    global_cfg: dict,
    state: PortfolioState,
    allocated_notional: float = 0.0,
    allocated_risk: float = 0.0,
    allocated_sector_notional: float = 0.0,
    allocated_buy_notional: float = 0.0,
) -> AllocationResult:
    """Cap a strategy-proposed quantity without ever increasing it.

    The function applies five independent limits: strategy/portfolio position
    weight, gross exposure, portfolio heat, sector exposure and cash reserve.
    The tightest constraint wins. Risk includes the configured round-trip
    friction so tiny stops cannot create implausibly large positions.
    """

    proposed = max(int(proposed_qty), 0)
    entry = max(_float(entry_price), 0.0)
    stop = max(_float(stop_price), 0.0)
    equity = max(_float(state.equity), 0.0)
    side_norm = str(side).upper()
    portfolio_cfg = global_cfg.get("portfolio", {})
    sizing_cfg = strategy_cfg.get("position_sizing", {})

    if proposed <= 0 or entry <= 0 or stop <= 0 or equity <= 0:
        return AllocationResult(
            quantity=0,
            entry_price=entry,
            notional_usd=0.0,
            risk_per_share_usd=0.0,
            risk_at_stop_usd=0.0,
            weight_pct_nav=0.0,
            portfolio_heat_after_pct_nav=state.open_risk / max(equity, 1e-12),
            gross_exposure_after_pct_nav=state.gross_exposure / max(equity, 1e-12),
            sector_exposure_after_pct_nav=state.sector_exposure.get(sector, 0.0)
            / max(equity, 1e-12),
            binding_limit="invalid_input",
            blocked_reason="Invalid quantity, entry, stop or equity",
        )

    friction = entry * _round_trip_cost_bps(global_cfg) / 10000.0
    risk_per_share = abs(entry - stop) + friction
    if risk_per_share <= 0:
        return AllocationResult(
            quantity=0,
            entry_price=entry,
            notional_usd=0.0,
            risk_per_share_usd=0.0,
            risk_at_stop_usd=0.0,
            weight_pct_nav=0.0,
            portfolio_heat_after_pct_nav=state.open_risk / equity,
            gross_exposure_after_pct_nav=state.gross_exposure / equity,
            sector_exposure_after_pct_nav=state.sector_exposure.get(sector, 0.0)
            / equity,
            binding_limit="invalid_risk",
            blocked_reason="Risk per share is zero",
        )

    max_position_pct = _float(
        sizing_cfg.get(
            "max_weight_pct_nav",
            portfolio_cfg.get("max_position_pct_nav", 1.0),
        ),
        1.0,
    )
    max_gross_pct = _float(portfolio_cfg.get("max_gross_exposure_pct_nav", 1.0), 1.0)
    max_heat_pct = _float(portfolio_cfg.get("max_portfolio_heat_pct_nav", 1.0), 1.0)
    max_sector_pct = _float(
        portfolio_cfg.get("max_sector_exposure_pct_nav", max_gross_pct),
        max_gross_pct,
    )
    min_cash_pct = _float(portfolio_cfg.get("min_cash_pct_nav", 0.0), 0.0)
    max_notional = _float(sizing_cfg.get("max_notional_usd"), 0.0)

    caps: list[tuple[str, int]] = [("strategy_proposal", proposed)]
    caps.append(_qty_cap("max_position", equity * max(max_position_pct, 0.0), entry))
    if max_notional > 0:
        caps.append(_qty_cap("max_notional", max_notional, entry))

    remaining_gross = (
        equity * max(max_gross_pct, 0.0)
        - state.gross_exposure
        - max(allocated_notional, 0.0)
    )
    caps.append(_qty_cap("max_gross_exposure", remaining_gross, entry))

    remaining_heat = (
        equity * max(max_heat_pct, 0.0)
        - state.open_risk
        - max(allocated_risk, 0.0)
    )
    caps.append(
        (
            "max_portfolio_heat",
            max(0, floor(max(remaining_heat, 0.0) / risk_per_share)),
        )
    )

    current_sector = max(_float(state.sector_exposure.get(sector, 0.0)), 0.0)
    remaining_sector = (
        equity * max(max_sector_pct, 0.0)
        - current_sector
        - max(allocated_sector_notional, 0.0)
    )
    caps.append(_qty_cap("max_sector_exposure", remaining_sector, entry))

    if side_norm == "BUY":
        cash_floor = equity * max(min_cash_pct, 0.0)
        spendable_cash = state.cash - cash_floor - max(allocated_buy_notional, 0.0)
        caps.append(_qty_cap("cash_reserve", spendable_cash, entry))

    binding_limit, final_qty = min(caps, key=lambda item: item[1])
    final_qty = max(0, min(final_qty, proposed))
    notional = final_qty * entry
    risk = final_qty * risk_per_share
    sector_after = current_sector + max(allocated_sector_notional, 0.0) + notional
    gross_after = state.gross_exposure + max(allocated_notional, 0.0) + notional
    heat_after = state.open_risk + max(allocated_risk, 0.0) + risk

    return AllocationResult(
        quantity=final_qty,
        entry_price=entry,
        notional_usd=notional,
        risk_per_share_usd=risk_per_share,
        risk_at_stop_usd=risk,
        weight_pct_nav=notional / equity,
        portfolio_heat_after_pct_nav=heat_after / equity,
        gross_exposure_after_pct_nav=gross_after / equity,
        sector_exposure_after_pct_nav=sector_after / equity,
        binding_limit=binding_limit,
        blocked_reason=(
            f"Portfolio risk limit leaves zero quantity ({binding_limit})"
            if final_qty <= 0
            else ""
        ),
    )
