from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


EVENT_LABELS = {
    "OPEN": "Apertura",
    "OPEN_PLUS_2H": "Apertura +2h",
    "OPEN_PLUS_4H": "Apertura +4h",
    "OPEN_PLUS_6H": "Apertura +6h",
    "CLOSE": "Cierre",
    "drift_alert": "Alerta de drift",
    "retrain_decision": "Decisión de reentrenamiento",
    "incident_alert": "Alerta de incidente",
}


@dataclass(frozen=True)
class MessageContext:
    event: str
    date: str
    top_picks: list[str] = field(default_factory=list)
    top_blocks: list[str] = field(default_factory=list)
    expected_floor: str | None = None
    expected_ceiling: str | None = None
    expected_bucket_or_business_day: str | None = None
    reward_risk: str | None = None
    strategy_action: str | None = None
    risk_changes_and_actions: str | None = None
    floor_m3: str | None = None
    floor_week_m3: str | None = None
    floor_week_m3_start_date: str | None = None
    floor_week_m3_end_date: str | None = None
    floor_week_m3_confidence: str | None = None
    m3_material_change: str | None = None
    m3_week_proximity: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def _format_line(label: str, value: str | list[str] | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        if not value:
            return None
        return f"{label}: {', '.join(value)}"
    stripped = str(value).strip()
    if not stripped:
        return None
    return f"{label}: {stripped}"


def build_message(ctx: MessageContext) -> str:
    if ctx.event not in EVENT_LABELS:
        raise ValueError(f"Unsupported event: {ctx.event}")

    header = f"[{ctx.event}] {EVENT_LABELS[ctx.event]} - {ctx.date}"
    lines = [header]

    ordered_lines = [
        _format_line("Top picks", ctx.top_picks),
        _format_line("Top blocks", ctx.top_blocks),
        _format_line("Piso esperado", ctx.expected_floor),
        _format_line("Techo esperado", ctx.expected_ceiling),
        _format_line("Bucket/día hábil esperado", ctx.expected_bucket_or_business_day),
        _format_line("Reward/Risk", ctx.reward_risk),
        _format_line("Acción recomendada por estrategia", ctx.strategy_action),
        _format_line("Cambios de riesgo y acciones tomadas", ctx.risk_changes_and_actions),
        _format_line("floor_m3", ctx.floor_m3),
        _format_line("floor_week_m3", ctx.floor_week_m3),
        _format_line("floor_week_m3_start_date", ctx.floor_week_m3_start_date),
        _format_line("floor_week_m3_end_date", ctx.floor_week_m3_end_date),
        _format_line("floor_week_m3_confidence", ctx.floor_week_m3_confidence),
        _format_line("m3_material_change", ctx.m3_material_change),
        _format_line("m3_week_proximity", ctx.m3_week_proximity),
    ]

    lines.extend([line for line in ordered_lines if line])

    for key, value in sorted(ctx.extra.items()):
        line = _format_line(key, str(value))
        if line:
            lines.append(line)

    return "\n".join(lines)
