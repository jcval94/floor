from __future__ import annotations

from dataclasses import dataclass
from typing import Any


EVENTS = {
    "OPEN",
    "OPEN_PLUS_2H",
    "OPEN_PLUS_4H",
    "OPEN_PLUS_6H",
    "CLOSE",
    "drift_alert",
    "retrain_decision",
    "incident_alert",
}


@dataclass(frozen=True)
class MessageContext:
    event: str
    date: str
    top_picks: list[str] | None = None
    top_blocks: list[str] | None = None
    expected_floor: str | None = None
    expected_ceiling: str | None = None
    expected_bucket_or_business_day: str | None = None
    reward_risk: str | None = None
    strategy_action: str | None = None
    risk_changes_and_actions: str | None = None
    extra: dict[str, Any] | None = None


def build_message(ctx: MessageContext) -> str:
    if ctx.event not in EVENTS:
        raise ValueError(f"Unsupported event: {ctx.event}")

    lines = [f"[{ctx.event}] {ctx.date}"]
    if ctx.top_picks:
        lines.append(f"Top picks: {', '.join(ctx.top_picks)}")
    if ctx.top_blocks:
        lines.append(f"Top blocks: {', '.join(ctx.top_blocks)}")
    if ctx.expected_floor:
        lines.append(f"Piso esperado: {ctx.expected_floor}")
    if ctx.expected_ceiling:
        lines.append(f"Techo esperado: {ctx.expected_ceiling}")
    if ctx.expected_bucket_or_business_day:
        lines.append(f"Bucket/día hábil esperado: {ctx.expected_bucket_or_business_day}")
    if ctx.reward_risk:
        lines.append(f"Reward/Risk: {ctx.reward_risk}")
    if ctx.strategy_action:
        lines.append(f"Acción recomendada por estrategia: {ctx.strategy_action}")
    if ctx.risk_changes_and_actions:
        lines.append(f"Cambios de riesgo y acciones tomadas: {ctx.risk_changes_and_actions}")
    if ctx.extra:
        for k, v in sorted(ctx.extra.items()):
            lines.append(f"{k}: {v}")

    return "\n".join(lines)
