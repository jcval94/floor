from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal, cast


Horizon = Literal["d1", "w1", "q1", "m3"]
EventType = Literal["OPEN", "OPEN_PLUS_2H", "OPEN_PLUS_4H", "OPEN_PLUS_6H", "CLOSE"]


MULTI_HORIZON_PREDICTION_CONTRACT: dict[str, object] = {
    "version": "v1",
    "horizons": ["d1", "w1", "q1", "m3"],
    "required_fields": {
        "d1": ["floor_value", "ceiling_value", "floor_time_bucket", "ceiling_time_bucket"],
        "w1": ["floor_value", "ceiling_value", "floor_time_bucket", "ceiling_time_bucket"],
        "q1": ["floor_value", "ceiling_value", "floor_time_bucket", "ceiling_time_bucket"],
        "m3": [
            "floor_m3",
            "floor_week_m3",
            "floor_week_m3_confidence",
            "floor_week_m3_top3",
            "m3_status",
            "m3_block_reason",
        ],
    },
    "score_fields": ["confidence_score", "floor_time_probability", "ceiling_time_probability"],
}


@dataclass
class PredictionRecord:
    symbol: str
    as_of: datetime
    event_type: EventType
    horizon: Horizon
    floor_value: float | None
    ceiling_value: float | None
    floor_quantile: float = 0.1
    ceiling_quantile: float = 0.9
    floor_time_bucket: str = ""
    ceiling_time_bucket: str = ""
    floor_time_probability: float = 0.0
    ceiling_time_probability: float = 0.0
    confidence_score: float = 0.0
    expected_return: float | None = None
    expected_range: float | None = None
    m3_payload: dict[str, Any] = field(default_factory=dict)
    floor_m3: float | None = None
    floor_week_m3: int | None = None
    floor_week_m3_confidence: float | None = None
    floor_week_m3_top3: list[dict[str, float]] = field(default_factory=list)
    floor_week_m3_start_date: str | None = None
    floor_week_m3_end_date: str | None = None
    floor_week_m3_label_human: str | None = None
    m3_status: str | None = None
    m3_block_reason: str | None = None
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
    data_drift: float = 0.0
    concept_drift: float = 0.0
    calibration_drift: float = 0.0
    performance_decay: float = 0.0
    thresholds: dict[str, Any] = field(default_factory=dict)
    action: str = "SKIP_RETRAIN"
    reason: str = ""
    model_key: str = ""
    champion_path: str = ""
    current_version: str = ""
    status: str = "OK"
    recommendation: str = "SKIP_RETRAIN"
    auto_retrain: bool = False
    drift_level: str = "GREEN"
    summary: dict[str, Any] = field(default_factory=dict)



def record_to_dict(record: object) -> dict[str, object]:
    payload = asdict(cast(Any, record))
    for k, v in payload.items():
        if isinstance(v, datetime):
            payload[k] = v.isoformat()
    return payload
