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

        equity_curve: list[dict] = []
        trades: list[dict] = []
        closed_trade_pnls: list[float] = []
        ticker_pnl: defaultdict[str, float] = defaultdict(float)
        ticker_turnover: defaultdict[str, float] = defaultdict(float)
        strategy_contrib: defaultdict[str, float] = defaultdict(float)
        partial_fill_count = 0
        total_order_count = 0

        prev_equity = self.initial_cash
        prev_prices: dict[str, float] = {}
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

                trade_realized = self._apply_trade(positions[ticker], filled_qty, fill_price)
                realized_pnl += trade_realized
                if trade_realized != 0:
                    closed_trade_pnls.append(trade_realized)

                if side == "BUY":
                    cash -= notional + costs["total_cost"]
                else:
                    cash += notional - costs["total_cost"]

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
                        "realized_pnl": trade_realized,
                        "turnover": turnover_piece,
                    }
                )

            equity = cash + sum(pos.qty * closes.get(ticker, 0.0) for ticker, pos in positions.items())
            unrealized_pnl = sum(
                pos.qty * (closes.get(ticker, 0.0) - pos.avg_cost)
                for ticker, pos in positions.items()
                if pos.qty != 0 and ticker in closes
            )

            peak_equity = max(peak_equity, equity)
            drawdown = 0.0 if peak_equity == 0 else (equity / peak_equity) - 1.0
            day_return = 0.0 if prev_equity == 0 else (equity / prev_equity) - 1.0

            self._accumulate_strategy_contrib(strategy_contrib, strategy_targets, date, prev_prices, closes, prev_equity)

            equity_curve.append(
                {
                    "date": date,
                    "equity": equity,
                    "cash": cash,
                    "realized_pnl": realized_pnl,
                    "unrealized_pnl": unrealized_pnl,
                    "daily_return": day_return,
                    "drawdown": drawdown,
                    "gross_exposure": self._gross_exposure(positions, closes, equity),
                }
            )

            prev_equity = equity
            prev_prices = closes

        turnover = sum(t["turnover"] for t in trades)
        fill_ratio = 0.0 if total_order_count == 0 else 1 - (partial_fill_count / total_order_count)
        capacity_warning = fill_ratio < 0.85 or turnover > 10

        return {
            "equity_curve": equity_curve,
            "trades": trades,
            "positions": {k: vars(v) for k, v in positions.items() if v.qty != 0},
            "realized_pnl": realized_pnl,
            "turnover": turnover,
            "closed_trade_pnls": closed_trade_pnls,
            "ticker_pnl": dict(ticker_pnl),
            "ticker_turnover": dict(ticker_turnover),
            "strategy_contribution": dict(strategy_contrib),
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
        strategy_targets: dict[str, dict[str, dict[str, float]]],
        date: str,
        prev_prices: dict[str, float],
        closes: dict[str, float],
        prev_equity: float,
    ) -> None:
        if prev_equity <= 0:
            return
        for strategy, by_date in strategy_targets.items():
            day_targets = by_date.get(date, {})
            pnl = 0.0
            for ticker, w in day_targets.items():
                if ticker not in prev_prices or ticker not in closes or prev_prices[ticker] == 0:
                    continue
                ret = (closes[ticker] / prev_prices[ticker]) - 1.0
                pnl += prev_equity * float(w) * ret
            contrib[strategy] += pnl
