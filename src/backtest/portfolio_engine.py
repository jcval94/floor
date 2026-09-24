from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from math import floor

from backtest.cost_model import CostModel
from backtest.execution_simulator import ExecutionSimulator


@dataclass
class Position:
    qty: int = 0
    avg_cost: float = 0.0
    # Transaction costs paid to establish the still-open quantity.  Keeping
    # them with the position lets realized/unrealized P&L reconcile exactly
    # with the cash ledger instead of reporting gross-of-cost trade stats.
    open_costs: float = 0.0


class PortfolioEngine:
    def __init__(
        self,
        cost_model: CostModel,
        execution_simulator: ExecutionSimulator,
        initial_cash: float,
        max_gross_exposure: float = 1.0,
        allow_short: bool = False,
        strategy_weights: dict[str, float] | None = None,
    ) -> None:
        self.cost_model = cost_model
        self.execution_simulator = execution_simulator
        self.initial_cash = float(initial_cash)
        self.max_gross_exposure = float(max_gross_exposure)
        self.allow_short = allow_short
        self.strategy_weights = strategy_weights or {}

    def run(
        self,
        market_data: list[dict],
        strategy_targets: dict[str, dict[str, dict[str, float]]],
    ) -> dict:
        grouped: dict[str, dict[str, dict]] = defaultdict(dict)
        for row in market_data:
            grouped[str(row["date"])][str(row["ticker"])] = row

        dates = sorted(grouped)
        if not dates:
            raise ValueError("market_data is empty")

        cash = self.initial_cash
        positions: dict[str, Position] = defaultdict(Position)
        realized_pnl = 0.0
        gross_realized_pnl = 0.0
        total_costs = 0.0

        equity_curve: list[dict] = []
        trades: list[dict] = []
        closed_trade_pnls: list[float] = []
        ticker_pnl: defaultdict[str, float] = defaultdict(float)
        ticker_turnover: defaultdict[str, float] = defaultdict(float)
        strategy_gross_contrib: defaultdict[str, float] = defaultdict(float)
        strategy_cost_allocation: defaultdict[str, float] = defaultdict(float)
        partial_fill_count = 0
        total_order_count = 0

        prev_equity = self.initial_cash
        prev_prices: dict[str, float] = {}
        previous_strategy_targets: dict[str, dict[str, float]] = {}
        peak_equity = self.initial_cash

        for date in dates:
            bars = grouped[date]
            closes = {t: float(v["close"]) for t, v in bars.items()}

            # mark-to-market from prior close to current close
            mtm_day = 0.0
            for ticker, pos in positions.items():
                if pos.qty == 0 or ticker not in closes or ticker not in prev_prices:
                    continue
                pnl = pos.qty * (closes[ticker] - prev_prices[ticker])
                mtm_day += pnl
                ticker_pnl[ticker] += pnl

            equity_pre_trade = cash + sum(pos.qty * closes.get(ticker, 0.0) for ticker, pos in positions.items())
            equity_pre_trade = max(equity_pre_trade, 0.0)

            # Attribute the close-to-close move to the exposures that were
            # actually carried into this session.  Same-session execution
            # effects and costs are attributed below when trades occur.
            self._accumulate_strategy_contrib(
                strategy_gross_contrib,
                previous_strategy_targets,
                prev_prices,
                closes,
                prev_equity,
            )

            combined_targets = self._combine_targets(strategy_targets, date)
            desired_targets = self._cap_targets(combined_targets)

            for ticker, target_weight in desired_targets.items():
                if ticker not in bars:
                    continue
                price = closes[ticker]
                if price <= 0:
                    continue

                desired_qty = floor((equity_pre_trade * target_weight) / price)
                if not self.allow_short:
                    desired_qty = max(desired_qty, 0)
                current_qty = positions[ticker].qty
                delta_qty = desired_qty - current_qty
                if delta_qty == 0:
                    continue

                total_order_count += 1
                fill = self.execution_simulator.simulate_fill(delta_qty, bars[ticker])
                filled_qty = int(fill["filled_qty"])
                if filled_qty == 0:
                    continue
                if fill["fill_ratio"] < 1:
                    partial_fill_count += 1

                side = "BUY" if filled_qty > 0 else "SELL"
                abs_qty = abs(filled_qty)
                fill_price = float(fill["fill_price"])
                costs = self.cost_model.estimate(side, abs_qty, fill_price)
                notional = abs_qty * fill_price

                # cash management: no margin for long buys.
                if side == "BUY":
                    max_affordable = floor(max(cash - costs["total_cost"], 0.0) / fill_price)
                    if max_affordable <= 0:
                        continue
                    if abs_qty > max_affordable:
                        abs_qty = max_affordable
                        filled_qty = abs_qty
                        notional = abs_qty * fill_price
                        costs = self.cost_model.estimate(side, abs_qty, fill_price)

                position = positions[ticker]
                old_qty = position.qty
                old_open_costs = position.open_costs
                trade_cost = float(costs["total_cost"])
                trade_abs_qty = abs(filled_qty)
                closes_existing = (
                    old_qty != 0
                    and filled_qty != 0
                    and (old_qty > 0) != (filled_qty > 0)
                )
                closing_qty = min(abs(old_qty), trade_abs_qty) if closes_existing else 0
                opening_qty = trade_abs_qty - closing_qty
                allocated_entry_cost = (
                    old_open_costs * closing_qty / abs(old_qty)
                    if closing_qty > 0 and old_qty != 0
                    else 0.0
                )
                closing_trade_cost = (
                    trade_cost * closing_qty / trade_abs_qty
                    if closing_qty > 0 and trade_abs_qty > 0
                    else 0.0
                )
                opening_trade_cost = trade_cost - closing_trade_cost

                trade_realized_gross = self._apply_trade(
                    position,
                    filled_qty,
                    fill_price,
                )
                trade_realized_net = (
                    trade_realized_gross
                    - allocated_entry_cost
                    - closing_trade_cost
                    if closing_qty > 0
                    else 0.0
                )
                gross_realized_pnl += trade_realized_gross
                realized_pnl += trade_realized_net
                if closing_qty > 0:
                    closed_trade_pnls.append(trade_realized_net)

                if position.qty == 0:
                    position.open_costs = 0.0
                elif closing_qty == 0:
                    position.open_costs = old_open_costs + trade_cost
                elif opening_qty > 0:
                    # Direction flip: old entry costs are fully realized and
                    # only the opening portion of this trade's cost remains.
                    position.open_costs = opening_trade_cost
                else:
                    position.open_costs = max(
                        0.0,
                        old_open_costs - allocated_entry_cost,
                    )

                if side == "BUY":
                    cash -= notional + trade_cost
                else:
                    cash += notional - trade_cost

                total_costs += trade_cost
                # The close-to-close MTM above values the pre-trade position at
                # today's close.  Adjust to the actual fill and charge costs so
                # ticker attribution reconciles with account equity.
                execution_adjustment = filled_qty * (price - fill_price)
                ticker_pnl[ticker] += execution_adjustment - trade_cost
                self._allocate_trade_effect(
                    strategy_gross_contrib,
                    strategy_cost_allocation,
                    strategy_targets,
                    previous_strategy_targets,
                    date,
                    ticker,
                    execution_adjustment,
                    trade_cost,
                )

                turnover_piece = 0.0 if prev_equity <= 0 else notional / prev_equity
                ticker_turnover[ticker] += turnover_piece

                trades.append(
                    {
                        "date": date,
                        "ticker": ticker,
                        "side": side,
                        "quantity": abs_qty,
                        "price": fill_price,
                        "costs": costs,
                        "gross_realized_pnl": trade_realized_gross,
                        "realized_pnl": trade_realized_net,
                        "turnover": turnover_piece,
                    }
                )

            equity = cash + sum(pos.qty * closes.get(ticker, 0.0) for ticker, pos in positions.items())
            gross_unrealized_pnl = sum(
                pos.qty * (closes.get(ticker, 0.0) - pos.avg_cost)
                for ticker, pos in positions.items()
                if pos.qty != 0 and ticker in closes
            )
            unrealized_pnl = gross_unrealized_pnl - sum(
                pos.open_costs
                for ticker, pos in positions.items()
                if pos.qty != 0 and ticker in closes
            )

            peak_equity = max(peak_equity, equity)
            drawdown = 0.0 if peak_equity == 0 else (equity / peak_equity) - 1.0
            day_return = 0.0 if prev_equity == 0 else (equity / prev_equity) - 1.0

            equity_curve.append(
                {
                    "date": date,
                    "equity": equity,
                    "cash": cash,
                    "realized_pnl": realized_pnl,
                    "gross_realized_pnl": gross_realized_pnl,
                    "unrealized_pnl": unrealized_pnl,
                    "gross_unrealized_pnl": gross_unrealized_pnl,
                    "costs_paid": total_costs,
                    "daily_return": day_return,
                    "drawdown": drawdown,
                    "gross_exposure": self._gross_exposure(positions, closes, equity),
                }
            )

            prev_equity = equity
            prev_prices = closes
            previous_strategy_targets = {
                strategy: {
                    ticker: self.strategy_weights.get(strategy, 1.0) * float(target)
                    for ticker, target in by_date.get(date, {}).items()
                }
                for strategy, by_date in strategy_targets.items()
            }

        turnover = sum(t["turnover"] for t in trades)
        fill_ratio = 0.0 if total_order_count == 0 else 1 - (partial_fill_count / total_order_count)
        capacity_warning = fill_ratio < 0.85 or turnover > 10
        strategy_contrib = {
            strategy: strategy_gross_contrib.get(strategy, 0.0)
            - strategy_cost_allocation.get(strategy, 0.0)
            for strategy in set(strategy_gross_contrib) | set(strategy_cost_allocation)
        }
        final_equity = equity_curve[-1]["equity"]
        final_unrealized = equity_curve[-1]["unrealized_pnl"]
        reconciliation_error = (
            (final_equity - self.initial_cash)
            - (realized_pnl + final_unrealized)
        )

        return {
            "equity_curve": equity_curve,
            "trades": trades,
            "positions": {k: vars(v) for k, v in positions.items() if v.qty != 0},
            "gross_realized_pnl": gross_realized_pnl,
            "realized_pnl": realized_pnl,
            "total_costs": total_costs,
            "pnl_reconciliation_error": reconciliation_error,
            "turnover": turnover,
            "closed_trade_pnls": closed_trade_pnls,
            "ticker_pnl": dict(ticker_pnl),
            "ticker_turnover": dict(ticker_turnover),
            "strategy_contribution": strategy_contrib,
            "strategy_contribution_gross": dict(strategy_gross_contrib),
            "strategy_cost_allocation": dict(strategy_cost_allocation),
            "capacity_warning": capacity_warning,
            "fill_efficiency": fill_ratio,
        }

    def _combine_targets(self, strategy_targets: dict[str, dict[str, dict[str, float]]], date: str) -> dict[str, float]:
        combined: defaultdict[str, float] = defaultdict(float)
        for strategy, by_date in strategy_targets.items():
            weight = self.strategy_weights.get(strategy, 1.0)
            for ticker, target in by_date.get(date, {}).items():
                combined[ticker] += weight * float(target)
        return dict(combined)

    def _cap_targets(self, targets: dict[str, float]) -> dict[str, float]:
        gross = sum(abs(x) for x in targets.values())
        if gross <= self.max_gross_exposure or gross == 0:
            return targets
        scale = self.max_gross_exposure / gross
        return {k: v * scale for k, v in targets.items()}

    def _gross_exposure(self, positions: dict[str, Position], closes: dict[str, float], equity: float) -> float:
        if equity <= 0:
            return 0.0
        gross_notional = sum(abs(p.qty * closes.get(ticker, 0.0)) for ticker, p in positions.items())
        return gross_notional / equity

    def _apply_trade(self, position: Position, signed_qty: int, price: float) -> float:
        if signed_qty == 0:
            return 0.0

        old_qty = position.qty
        old_avg = position.avg_cost
        new_qty = old_qty + signed_qty

        # Same direction or opening.
        if old_qty == 0 or (old_qty > 0 and signed_qty > 0) or (old_qty < 0 and signed_qty < 0):
            total_notional = old_avg * abs(old_qty) + price * abs(signed_qty)
            position.qty = new_qty
            position.avg_cost = total_notional / abs(new_qty)
            return 0.0

        closing_qty = min(abs(old_qty), abs(signed_qty))
        realized = closing_qty * (price - old_avg) * (1 if old_qty > 0 else -1)

        if new_qty == 0:
            position.qty = 0
            position.avg_cost = 0.0
        elif (old_qty > 0 > new_qty) or (old_qty < 0 < new_qty):
            # flipped direction: remaining opens at current price
            position.qty = new_qty
            position.avg_cost = price
        else:
            position.qty = new_qty
        return realized

    def _accumulate_strategy_contrib(
        self,
        contrib: dict[str, float],
        previous_targets: dict[str, dict[str, float]],
        prev_prices: dict[str, float],
        closes: dict[str, float],
        prev_equity: float,
    ) -> None:
        if prev_equity <= 0:
            return
        for strategy, day_targets in previous_targets.items():
            pnl = 0.0
            for ticker, weighted_target in day_targets.items():
                if ticker not in prev_prices or ticker not in closes or prev_prices[ticker] == 0:
                    continue
                ret = (closes[ticker] / prev_prices[ticker]) - 1.0
                pnl += prev_equity * float(weighted_target) * ret
            contrib[strategy] += pnl

    def _allocate_trade_effect(
        self,
        gross_contrib: dict[str, float],
        cost_allocation: dict[str, float],
        strategy_targets: dict[str, dict[str, dict[str, float]]],
        previous_targets: dict[str, dict[str, float]],
        date: str,
        ticker: str,
        execution_adjustment: float,
        trade_cost: float,
    ) -> None:
        exposures: dict[str, float] = {}
        for strategy, by_date in strategy_targets.items():
            target = float(by_date.get(date, {}).get(ticker, 0.0))
            weighted = self.strategy_weights.get(strategy, 1.0) * target
            if abs(weighted) > 1e-12:
                exposures[strategy] = weighted

        # Exits commonly arrive as an explicit zero target.  Attribute their
        # execution drag/cost to the strategy that carried the exposure into
        # the session rather than dropping the cost on the floor.
        if not exposures:
            for strategy, day_targets in previous_targets.items():
                weighted = float(day_targets.get(ticker, 0.0))
                if abs(weighted) > 1e-12:
                    exposures[strategy] = weighted

        denominator = sum(abs(value) for value in exposures.values())
        if denominator <= 0:
            return
        for strategy, exposure in exposures.items():
            share = abs(exposure) / denominator
            gross_contrib[strategy] += execution_adjustment * share
            cost_allocation[strategy] += trade_cost * share
