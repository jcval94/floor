from __future__ import annotations

from datetime import datetime, timezone


def ai_recency_weight(recency_days: int | None, fresh_days: int = 2, stale_days: int = 7) -> float:
    if recency_days is None:
        return 0.5
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
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def merge_market_with_ai_signal(market_row: dict, ai_row: dict | None, as_of: datetime | None = None) -> dict:
    merged = dict(market_row)
    as_of = as_of or datetime.now(tz=timezone.utc)
    ai_row = ai_row or {}

    for k in [
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
        if k in ai_row and ai_row[k] not in (None, ""):
            merged[k] = ai_row[k]

    recency = ai_row.get("ai_recency")
    if recency is None:
        updated_at = _parse_iso(ai_row.get("ai_updated_at"))
        if updated_at is not None:
            recency = max(0, (as_of.date() - updated_at.date()).days)
    merged["ai_recency"] = recency
    merged["ai_weight"] = round(ai_recency_weight(recency), 4)

    consensus = float(merged.get("ai_consensus_score") or 0.0)
    conviction = float(merged.get("ai_conviction") or 0.5)
    merged["ai_effective_score"] = round(consensus * conviction * merged["ai_weight"], 6)
    return merged
