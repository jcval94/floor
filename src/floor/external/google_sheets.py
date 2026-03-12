from __future__ import annotations

import csv
from dataclasses import dataclass
from io import StringIO
from urllib.request import urlopen


@dataclass(frozen=True)
class ExternalRecommendation:
    symbol: str
    action: str
    confidence: float
    note: str


def fetch_recommendations(csv_url: str | None) -> list[ExternalRecommendation]:
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
        rows.append(
            ExternalRecommendation(
                symbol=str(row["symbol"]).upper(),
                action=str(row["action"]).upper(),
                confidence=float(row["confidence"]),
                note=str(row["note"]),
            )
        )
    return rows
