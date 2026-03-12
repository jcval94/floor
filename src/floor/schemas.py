from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Literal

Horizon = Literal["d1", "w1", "q1"]
EventType = Literal["OPEN", "OPEN_PLUS_2H", "OPEN_PLUS_4H", "OPEN_PLUS_6H", "CLOSE"]


@dataclass
class PredictionRecord:
    symbol: str
    as_of: datetime
    event_type: EventType
    horizon: Horizon
    floor_value: float
    ceiling_value: float
    floor_quantile: float = 0.1
    ceiling_quantile: float = 0.9
    floor_time_bucket: str = ""
    ceiling_time_bucket: str = ""
    floor_time_probability: float = 0.0
    ceiling_time_probability: float = 0.0
    model_version: str = ""


@dataclass
class SignalRecord:
    symbol: str
    as_of: datetime
    horizon: Horizon
    action: Literal["BUY", "SELL", "HOLD"]
    confidence: float
    rationale: str


@dataclass
class OrderRecord:
    symbol: str
    as_of: datetime
    action: Literal["BUY", "SELL"]
    qty: int
    order_type: Literal["MKT", "LMT"]
    mode: Literal["PAPER", "LIVE"]


@dataclass
class TrainingReviewRecord:
    as_of: datetime
    model_name: str
    data_drift: float
    concept_drift: float
    calibration_drift: float
    performance_decay: float
    thresholds: dict[str, float] = field(default_factory=dict)
    action: Literal["RETRAIN", "SKIP"] = "SKIP"
    reason: str = ""


def record_to_dict(record: object) -> dict:
    payload = asdict(record)
    for k, v in payload.items():
        if isinstance(v, datetime):
            payload[k] = v.isoformat()
    return payload
