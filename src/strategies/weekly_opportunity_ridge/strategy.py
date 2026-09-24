from __future__ import annotations

import math

from strategies.base import StrategyDecision
from strategies.common import (
    alpha_hurdle,
    geometry,
    hold_decision,
    liquidity_ok,
    payoff_room_clears_cost,
    risk_sized_qty,
    to_float,
)

STRATEGY_ID = "weekly_opportunity_ridge"


def generate_weekly_opportunity_orders(
    rows: list[dict],
    global_cfg: dict,
    strategy_cfg: dict,
    session: str,
    held_symbols: set[str] | None = None,
) -> list[StrategyDecision]:
    del session

    held_symbols = {str(symbol) for symbol in (held_symbols or set())}
    score_field = str(
        strategy_cfg.get("model_score_field")
        or "weekly_opportunity_score"
    )
    entry = strategy_cfg.get("entry", {})
    buy_fraction = max(
        0.0,
        min(1.0, to_float(entry.get("buy_top_fraction"), 0.10)),
    )
    retain_fraction = max(
        buy_fraction,
        min(1.0, to_float(entry.get("retain_top_fraction"), 0.20)),
    )
    sell_fraction = max(
        0.0,
        min(1.0, to_float(entry.get("sell_bottom_fraction"), 0.10)),
    )
    min_buy_score = to_float(entry.get("min_buy_score"), 0.0)
    max_sell_score = to_float(entry.get("max_sell_score"), 0.0)
    min_rr = to_float(entry.get("min_reward_risk"), 1.2)
    buffer = to_float(
        strategy_cfg.get("risk", {}).get("stop_buffer_pct"),
        0.0,
    )

    scored: list[tuple[dict, float]] = []
    for row in rows:
        raw_score = row.get(score_field)
        if raw_score in (None, ""):
            continue
        score = to_float(raw_score, float("nan"))
        if math.isfinite(score):
            scored.append((row, score))

    if not scored:
        return []

    descending = sorted(
        scored,
        key=lambda item: item[1],
        reverse=True,
    )
    buy_count = (
        max(1, math.ceil(len(descending) * buy_fraction))
        if buy_fraction > 0
        else 0
    )
    retain_count = (
        max(buy_count, math.ceil(len(descending) * retain_fraction))
        if retain_fraction > 0
        else buy_count
    )
    sell_count = (
        max(1, math.ceil(len(descending) * sell_fraction))
        if sell_fraction > 0
        else 0
    )
    new_buy_symbols = {
        str(row["symbol"])
        for row, score in descending[:buy_count]
        if score > min_buy_score
    }
    retained_buy_symbols = {
        str(row["symbol"])
        for row, score in descending[:retain_count]
        if str(row["symbol"]) in held_symbols and score > min_buy_score
    }
    buy_symbols = new_buy_symbols | retained_buy_symbols
    sell_symbols = {
        str(row["symbol"])
        for row, score in descending[-sell_count:]
        if score < max_sell_score
    }

    output: list[StrategyDecision] = []
    for row, model_score in scored:
        current_geometry = geometry(row, "q1")
        symbol = str(row["symbol"])
        downside_scale = max(0.01, to_float(current_geometry["down"]))
        model_implied_signed_return = model_score * downside_scale
        long_alpha_ok, long_alpha = alpha_hurdle(
            max(0.0, model_implied_signed_return),
            global_cfg,
            strategy_cfg,
        )
        short_alpha_ok, short_alpha = alpha_hurdle(
            max(0.0, -model_implied_signed_return),
            global_cfg,
            strategy_cfg,
        )

        action = "HOLD"
        reward_risk = 0.0
        payoff_room = 0.0
        alpha = long_alpha
        if (
            symbol in buy_symbols
            and current_geometry["long_rr"] >= min_rr
            and payoff_room_clears_cost(
                current_geometry["up"], global_cfg, strategy_cfg
            )
            and long_alpha_ok
        ):
            action = "BUY"
            payoff_room = current_geometry["up"]
            reward_risk = current_geometry["long_rr"]
            alpha = long_alpha
        elif (
            symbol in sell_symbols
            and current_geometry["short_rr"] >= min_rr
            and payoff_room_clears_cost(
                current_geometry["down"], global_cfg, strategy_cfg
            )
            and short_alpha_ok
        ):
            action = "SELL"
            payoff_room = current_geometry["down"]
            reward_risk = current_geometry["short_rr"]
            alpha = short_alpha

        if action == "HOLD" or not liquidity_ok(row, strategy_cfg):
            output.append(
                hold_decision(
                    STRATEGY_ID,
                    row,
                    "q1",
                    (
                        f"HOLD: weekly score={model_score:.4f}, implied_return="
                        f"{model_implied_signed_return:.2%} does not clear "
                        "cost-aware alpha/payoff gate"
                    ),
                )
            )
            continue

        if action == "BUY":
            stop = current_geometry["risk_floor"] * (1 - buffer)
            take_profit = current_geometry["ceiling"]
        else:
            stop = current_geometry["risk_ceiling"] * (1 + buffer)
            take_profit = current_geometry["floor"]

        qty = risk_sized_qty(row, strategy_cfg, global_cfg, stop)
        if qty <= 0:
            output.append(
                hold_decision(
                    STRATEGY_ID,
                    row,
                    "q1",
                    "HOLD: zero risk-sized quantity",
                )
            )
            continue

        score = max(0.0, alpha["net_alpha_pct"]) * min(reward_risk, 3.0)
        retention = symbol in retained_buy_symbols and symbol not in new_buy_symbols
        output.append(
            StrategyDecision(
                strategy_id=STRATEGY_ID,
                symbol=symbol,
                side=action,
                score=score,
                qty=qty,
                horizon="q1",
                entry_reason=(
                    f"{action}: weekly Ridge score={model_score:.4f}, "
                    f"model_implied_return={model_implied_signed_return:.2%}, "
                    f"net_alpha={alpha['net_alpha_pct']:.2%}, "
                    f"payoff_room={payoff_room:.2%}, rr={reward_risk:.2f}"
                    + (" · retained_by_hysteresis" if retention else "")
                ),
                exit_reason="Q1 anchor or ten-session timeout",
                stop_price=stop,
                take_profit_price=take_profit,
                expected_return=model_implied_signed_return,
                expected_range=max(
                    0.0,
                    current_geometry["ceiling"] - current_geometry["floor"],
                ),
                timing_alignment=0.5,
                gross_alpha_pct=alpha["gross_alpha_pct"],
                net_alpha_pct=alpha["net_alpha_pct"],
                cost_pct=alpha["cost_pct"],
                alpha_source="weekly_ridge_score_x_predicted_q1_downside",
                payoff_room_pct=payoff_room,
            )
        )

    return output
