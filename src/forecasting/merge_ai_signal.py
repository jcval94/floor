from __future__ import annotations

from datetime import datetime, timezone


def ai_recency_weight(
    recency_days: int | None,
    fresh_days: int = 2,
    stale_days: int = 7,
) -> float:
    """Weight an actual AI signal by recency; missing AI has zero weight."""
    if recency_days is None:
        return 0.0
    if recency_days <= fresh_days:
        return 1.0
    if recency_days >= stale_days:
        return 0.35
    span = stale_days - fresh_days
    return 1.0 - ((recency_days - fresh_days) / span) * 0.65


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def merge_market_with_ai_signal(
    market_row: dict,
    ai_row: dict | None,
    as_of: datetime | None = None,
) -> dict:
    merged = dict(market_row)
    as_of = as_of or datetime.now(tz=timezone.utc)
    has_ai = bool(ai_row)
    source = ai_row or {}

    for key in [
        "ai_action",
        "ai_conviction",
        "ai_floor_d1",
        "ai_ceiling_d1",
        "ai_floor_w1",
        "ai_ceiling_w1",
        "ai_floor_q1",
        "ai_ceiling_q1",
        "ai_consensus_score",
        "ai_note",
    ]:
        if key in source and source[key] not in (None, ""):
            merged[key] = source[key]

    recency = source.get("ai_recency") if has_ai else None
    if has_ai and recency is None:
        updated_at = _parse_iso(source.get("ai_updated_at"))
        if updated_at is not None:
            recency = max(0, (as_of.date() - updated_at.date()).days)

    weight = ai_recency_weight(int(recency) if recency is not None else None) if has_ai else 0.0
    consensus = float(merged.get("ai_consensus_score") or 0.0) if has_ai else 0.0
    conviction = float(merged.get("ai_conviction") or 0.0) if has_ai else 0.0

    merged["ai_present"] = has_ai
    merged["ai_recency"] = recency
    merged["ai_weight"] = round(weight, 4)
    merged["ai_effective_score"] = round(consensus * conviction * weight, 6)
    merged["ai_horizon_alignment"] = float(merged.get("ai_horizon_alignment") or 0.0)
    return merged
