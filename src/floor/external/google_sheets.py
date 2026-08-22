from __future__ import annotations

import csv
from dataclasses import dataclass
from io import StringIO
from urllib.request import urlopen

ALLOWED_ACTIONS = {"BUY", "SELL", "HOLD"}
MIN_OVERRIDE_CONFIDENCE = 0.80


@dataclass(frozen=True)
class ExternalRecommendation:
    symbol: str
    action: str
    confidence: float
    note: str


def fetch_recommendations(csv_url: str | None) -> list[ExternalRecommendation]:
    """Load only well-formed, high-confidence external recommendations.

    The sheet is an advisory input, not an authenticated execution source. Low-confidence,
    malformed, or unsupported rows are dropped before they can reach the signal pipeline.
    LIVE trading is independently hard-blocked by RuntimeConfig.
    """

    if not csv_url:
        return []
    try:
        with urlopen(csv_url, timeout=10) as response:
            raw = response.read().decode("utf-8")
    except Exception:
        return []

    reader = csv.DictReader(StringIO(raw))
    required = {"symbol", "action", "confidence", "note"}
    if not required.issubset(set(reader.fieldnames or [])):
        return []

    rows: list[ExternalRecommendation] = []
    for row in reader:
        symbol = str(row.get("symbol") or "").strip().upper()
        action = str(row.get("action") or "").strip().upper()
        note = str(row.get("note") or "").strip()
        try:
            confidence = float(row.get("confidence") or "")
        except (TypeError, ValueError):
            continue

        if not symbol or action not in ALLOWED_ACTIONS:
            continue
        if not 0.0 <= confidence <= 1.0:
            continue
        if confidence < MIN_OVERRIDE_CONFIDENCE:
            continue

        rows.append(
            ExternalRecommendation(
                symbol=symbol,
                action=action,
                confidence=confidence,
                note=note,
            )
        )
    return rows
