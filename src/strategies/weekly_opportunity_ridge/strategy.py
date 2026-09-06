from __future__ import annotations

import math

from strategies.base import StrategyDecision
from strategies.common import (
    geometry,
    hold_decision,
    liquidity_ok,
    net_edge,
    risk_sized_qty,
    to_float,
)

STRATEGY_ID = "weekly_opportunity_ridge"


def generate_weekly_opportunity_orders(
    rows: list[dict],
    global_cfg: dict,
    strategy_cfg: dict,
    session: str,
) -> list[StrategyDecision]:
    del session

    score_field = str(
        strategy_cfg.get("model_score_field")
        or "weekly_opportunity_score"
    )
    entry = strategy_cfg.get("entry", {})
    buy_fraction = max(
        0.0,
        min(1.0, to_float(entry.get("buy_top_fraction"), 0.2)),
    )
    sell_fraction = max(
        0.0,
        min(1.0, to_float(entry.get("sell_bottom_fraction"), 0.2)),
    )
    min_buy_score = to_float(entry.get("min_buy_score"), 0.0)
    max_sell_score = to_float(entry.get("max_sell_score"), 0.0)
    min_rr = to_float(entry.get("min_reward_risk"), 1.2)
    min_net = to_float(entry.get("min_net_edge_pct"), 0.004)
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
    sell_count = (
        max(1, math.ceil(len(descending) * sell_fraction))
        if sell_fraction > 0
        else 0
    )
    buy_symbols = {
        str(row["symbol"])
        for row, score in descending[:buy_count]
        if score > min_buy_score
    }
    sell_symbols = {
        str(row["symbol"])
        for row, score in descending[-sell_count:]
        if score < max_sell_score
    }

    output: list[StrategyDecision] = []
    for row, model_score in scored:
        current_geometry = geometry(row, "q1")
        symbol = str(row["symbol"])
        action = "HOLD"
        gross_edge = 0.0
        reward_risk = 0.0

        if (
            symbol in buy_symbols
            and current_geometry["long_rr"] >= min_rr
            and net_edge(current_geometry["up"], global_cfg) >= min_net
        ):
            action = "BUY"
            gross_edge = current_geometry["up"]
            reward_risk = current_geometry["long_rr"]
        elif (
            symbol in sell_symbols
            and current_geometry["short_rr"] >= min_rr
            and net_edge(current_geometry["down"], global_cfg) >= min_net
        ):
            action = "SELL"
            gross_edge = current_geometry["down"]
            reward_risk = current_geometry["short_rr"]

        if action == "HOLD" or not liquidity_ok(row, strategy_cfg):
            output.append(
                hold_decision(
                    STRATEGY_ID,
                    row,
                    "q1",
                    (
                        f"HOLD: weekly score={model_score:.4f} is not a "
                        "cost-valid tail opportunity"
                    ),
                )
            )
            continue

        if action == "BUY":
            stop = current_geometry["floor"] * (1 - buffer)
            take_profit = current_geometry["ceiling"]
        else:
            stop = current_geometry["ceiling"] * (1 + buffer)
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

        model_scale = min(abs(model_score), 3.0) / 3.0
        score = (
            max(0.0, net_edge(gross_edge, global_cfg))
            * min(reward_risk, 3.0)
            * model_scale
        )
        output.append(
            StrategyDecision(
                strategy_id=STRATEGY_ID,
                symbol=symbol,
                side=action,
                score=score,
                qty=qty,
                horizon="q1",
                entry_reason=(
                    f"{action}: weekly Ridge tail score={model_score:.4f}, "
                    f"rr={reward_risk:.2f}, "
                    f"net_edge={net_edge(gross_edge, global_cfg):.2%}"
                ),
                exit_reason="Q1 anchor or ten-session timeout",
                stop_price=stop,
                take_profit_price=take_profit,
                expected_return=0.0,
                expected_range=max(
                    0.0,
                    current_geometry["ceiling"] - current_geometry["floor"],
                ),
                timing_alignment=0.5,
            )
        )

    return output
