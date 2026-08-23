from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from features.build_training_from_db import build_rows_from_db
from features.feature_builder import build_features
from floor.config import RuntimeConfig
from floor.schemas import PredictionRecord, SignalRecord

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")

MIN_SIGNAL_CONFIDENCE = 0.55
EXPECTED_RETURN_THRESHOLD = 0.01
LFS_POINTER_HEADER = "version https://git-lfs.github.com/spec/v1"
D1_TIMING = {"", "OPEN", "OPEN_PLUS_2H", "OPEN_PLUS_4H", "OPEN_PLUS_6H", "CLOSE"}
MODEL_INPUT_FIELDS = (
    "timestamp",
    "symbol",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "vwap_distance",
    "intraday_range_5",
    "rolling_vol_20",
    "atr_14",
    "momentum_10",
    "momentum_20",
    "relative_volume_20",
    "dist_to_low_20",
    "dist_to_high_20",
    "trend_context_m3",
)


def _looks_like_lfs_pointer(path: Path) -> bool:
    if not path.exists() or not path.is_file():
        return False
    try:
        head = path.read_text(encoding="utf-8")[:120]
    except UnicodeDecodeError:
        return False
    return head.startswith(LFS_POINTER_HEADER)


def _log_model_registry_preflight(cfg: RuntimeConfig) -> None:
    registry = cfg.data_dir / "training" / "models"
    models_file = cfg.data_dir / "training" / "models_file"
    candidates = [
        registry / "d1_champion.json",
        registry / "w1_champion.json",
        registry / "q1_champion.json",
        registry / "value_champion.json",
        registry / "timing_champion.json",
        models_file / "d1_champion.pkl",
        models_file / "w1_champion.pkl",
        models_file / "q1_champion.pkl",
        models_file / "value_champion.pkl",
        models_file / "timing_champion.pkl",
    ]
    diagnostics: list[dict[str, object]] = []
    for path in candidates:
        diagnostics.append(
            {
                "path": str(path),
                "exists": path.exists(),
                "is_lfs_pointer": _looks_like_lfs_pointer(path),
                "size": path.stat().st_size if path.exists() else None,
            }
        )
    logger.info(
        "[predictions][model-preflight] registry=%s registry_exists=%s models_file=%s models_file_exists=%s diagnostics=%s",
        registry,
        registry.exists(),
        models_file,
        models_file.exists(),
        diagnostics,
    )


def _model_input_snapshot(row: dict, ai_context: dict[str, Any] | None) -> dict[str, Any]:
    model_inputs = {field: row.get(field) for field in MODEL_INPUT_FIELDS}
    if ai_context:
        model_inputs["ai_context"] = {
            "ai_action": ai_context.get("ai_action"),
            "ai_conviction": ai_context.get("ai_conviction"),
            "ai_consensus_score": ai_context.get("ai_consensus_score"),
            "ai_note": ai_context.get("ai_note"),
        }
    return model_inputs


def _model_output_snapshot(row: dict) -> dict[str, Any]:
    return {
        "symbol": row.get("symbol"),
        "model_version": row.get("model_version"),
        "d1": {"floor": row.get("floor_d1"), "ceiling": row.get("ceiling_d1")},
        "w1": {"floor": row.get("floor_w1"), "ceiling": row.get("ceiling_w1")},
        "q1": {"floor": row.get("floor_q1"), "ceiling": row.get("ceiling_q1")},
        "m3": {
            "floor": row.get("floor_m3"),
            "week": row.get("floor_week_m3"),
            "status": row.get("m3_status"),
            "block_reason": row.get("m3_block_reason"),
        },
        "confidence_score": row.get("confidence_score"),
        "composite_signal_score": row.get("composite_signal_score"),
    }


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _time_text(value: object) -> str:
    return "" if value in (None, "") else str(value)


def _horizon_confidence(row: dict, horizon: Literal["d1", "w1", "q1"], default: float) -> float:
    breach_prob = _to_optional_float(row.get(f"breach_prob_{horizon}"))
    if breach_prob is None:
        return max(0.0, min(1.0, default))
    return max(0.0, min(1.0, 1.0 - breach_prob))


def _signal_from_prediction(
    symbol: str,
    horizon: Literal["d1", "w1", "q1", "m3"],
    floor: float,
    ceiling: float,
    expected_return: float | None,
    confidence_score: float | None,
    composite_signal_score: float | None,
) -> SignalRecord:
    del composite_signal_score  # directional score is not a calibrated probability
    confidence = max(0.0, min(_to_float(confidence_score, 0.0), 1.0))
    expected_ret = _to_float(expected_return, 0.0)
    action: Literal["BUY", "SELL", "HOLD"] = "HOLD"
    if confidence >= MIN_SIGNAL_CONFIDENCE and expected_ret >= EXPECTED_RETURN_THRESHOLD:
        action = "BUY"
    elif confidence >= MIN_SIGNAL_CONFIDENCE and expected_ret <= -EXPECTED_RETURN_THRESHOLD:
        action = "SELL"
    return SignalRecord(
        symbol=symbol,
        as_of=datetime.now(tz=ET),
        horizon=horizon,
        action=action,
        confidence=round(confidence, 4),
        rationale=(
            "Expected return + model confidence decision "
            f"(expected_return={expected_ret:.4f}, confidence={confidence:.4f}, "
            f"threshold={EXPECTED_RETURN_THRESHOLD:.4f}, min_confidence={MIN_SIGNAL_CONFIDENCE:.2f})"
        ),
    )


def _latest_feature_rows(cfg: RuntimeConfig, symbols: list[str]) -> list[dict]:
    symbol_set = {symbol.upper() for symbol in symbols}
    raw_rows = build_rows_from_db(
        db_path=cfg.data_dir / "market" / "market_data.sqlite",
        universe_path=cfg.root_dir / "config" / "universe.yaml",
    )
    selected = [row for row in raw_rows if str(row.get("symbol", "")).upper() in symbol_set]
    featured = build_features(selected)
    latest_by_symbol: dict[str, dict] = {}
    for row in featured:
        latest_by_symbol[str(row["symbol"]).upper()] = row
    missing = [symbol for symbol in symbols if symbol.upper() not in latest_by_symbol]
    if missing:
        logger.warning("[predictions] missing latest feature rows symbols=%s", ",".join(missing[:20]))
    return [latest_by_symbol[symbol.upper()] for symbol in symbols if symbol.upper() in latest_by_symbol]


def _validate_feature_rows(feature_rows: list[dict]) -> None:
    if not feature_rows:
        raise RuntimeError("Feature rows validation failed: empty input")
    malformed = 0
    for row in feature_rows:
        ts = row.get("timestamp")
        if not isinstance(ts, str) or not ts:
            malformed += 1
            continue
        try:
            datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            malformed += 1
    if malformed:
        raise RuntimeError(f"Feature rows validation failed: malformed timestamps={malformed}")


def _validate_timing_domain(horizon: str, payload: dict) -> None:
    floor_time = _time_text(payload.get("floor_time_bucket"))
    ceiling_time = _time_text(payload.get("ceiling_time_bucket"))
    if horizon == "d1":
        if floor_time not in D1_TIMING or ceiling_time not in D1_TIMING:
            raise RuntimeError(
                f"Prediction timing invalid horizon=d1 floor={floor_time!r} ceiling={ceiling_time!r}"
            )
        return
    if horizon in {"w1", "q1"}:
        upper = 5 if horizon == "w1" else 10
        for side, raw in (("floor", floor_time), ("ceiling", ceiling_time)):
            try:
                value = int(raw)
            except (TypeError, ValueError) as exc:
                raise RuntimeError(
                    f"Prediction timing invalid horizon={horizon} side={side}: {raw!r}"
                ) from exc
            if not 1 <= value <= upper:
                raise RuntimeError(
                    f"Prediction timing out of domain horizon={horizon} side={side}: {value} not in 1..{upper}"
                )
        return
    if horizon == "m3" and str(payload.get("m3_status") or "").lower() == "ok":
        try:
            week = int(floor_time)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"Prediction timing invalid horizon=m3: {floor_time!r}") from exc
        if not 1 <= week <= 13:
            raise RuntimeError(f"Prediction timing out of domain horizon=m3: {week} not in 1..13")


def _validate_prediction_payload(symbol: str, horizon: str, payload: dict) -> None:
    floor_value = payload.get("floor_value")
    ceiling_value = payload.get("ceiling_value")
    confidence = _to_float(payload.get("confidence_score"), -1.0)
    expected_range = payload.get("expected_range")
    if floor_value is None and horizon != "m3":
        raise RuntimeError(f"Prediction payload invalid symbol={symbol} horizon={horizon}: floor_value missing")
    if ceiling_value is None and horizon in {"d1", "w1", "q1"}:
        raise RuntimeError(f"Prediction payload invalid symbol={symbol} horizon={horizon}: ceiling_value missing")
    if floor_value is not None and ceiling_value is not None and float(floor_value) > float(ceiling_value):
        raise RuntimeError(f"Prediction payload invalid symbol={symbol} horizon={horizon}: floor_value > ceiling_value")
    if not 0.0 <= confidence <= 1.0:
        raise RuntimeError(f"Prediction payload invalid symbol={symbol} horizon={horizon}: confidence out of range")
    if expected_range is not None and float(expected_range) < 0:
        raise RuntimeError(f"Prediction payload invalid symbol={symbol} horizon={horizon}: negative expected_range")
    _validate_timing_domain(horizon, payload)


def _prediction_payloads(
    row: dict,
    event_type: str,
) -> list[tuple[Literal["d1", "w1", "q1", "m3"], dict]]:
    confidence = _to_float(row.get("confidence_score"), 0.0)
    d1_confidence = _horizon_confidence(row, "d1", confidence)
    w1_confidence = _horizon_confidence(row, "w1", confidence)
    q1_confidence = _horizon_confidence(row, "q1", confidence)

    d1_floor_time = _time_text(row.get("floor_time_bucket_d1"))
    d1_ceiling_time = _time_text(row.get("ceiling_time_bucket_d1"))
    m3_payload = {
        "floor_m3": row.get("floor_m3"),
        "floor_week_m3": row.get("floor_week_m3"),
        "floor_week_m3_confidence": row.get("floor_week_m3_confidence"),
        "floor_week_m3_top3": row.get("floor_week_m3_top3", []),
        "floor_week_m3_start_date": row.get("floor_week_m3_start_date"),
        "floor_week_m3_end_date": row.get("floor_week_m3_end_date"),
        "floor_week_m3_label_human": row.get("floor_week_m3_label_human"),
        "expected_return_m3": row.get("expected_return_m3"),
        "expected_range_m3": row.get("expected_range_m3"),
        "m3_status": row.get("m3_status"),
        "m3_block_reason": row.get("m3_block_reason"),
    }
    shared_m3 = dict(m3_payload)
    payloads: list[tuple[Literal["d1", "w1", "q1", "m3"], dict]] = []
    for horizon, floor_key, ceiling_key, floor_time, ceiling_time, horizon_conf in (
        ("d1", "floor_d1", "ceiling_d1", d1_floor_time, d1_ceiling_time, d1_confidence),
        ("w1", "floor_w1", "ceiling_w1", _time_text(row.get("floor_day_w1")), _time_text(row.get("ceiling_day_w1")), w1_confidence),
        ("q1", "floor_q1", "ceiling_q1", _time_text(row.get("floor_day_q1")), _time_text(row.get("ceiling_day_q1")), q1_confidence),
    ):
        time_probability = 0.0 if horizon == "d1" and (not floor_time or not ceiling_time) else horizon_conf
        payloads.append(
            (
                horizon,
                {
                    "floor_value": _to_optional_float(row.get(floor_key)),
                    "ceiling_value": _to_optional_float(row.get(ceiling_key)),
                    "floor_time_bucket": floor_time,
                    "ceiling_time_bucket": ceiling_time,
                    "floor_time_probability": time_probability,
                    "ceiling_time_probability": time_probability,
                    "confidence_score": horizon_conf,
                    "expected_return": _to_optional_float(row.get(f"expected_return_{horizon}")),
                    "expected_range": _to_optional_float(row.get(f"expected_range_{horizon}")),
                    "composite_signal_score": row.get(f"composite_signal_score_{horizon}", row.get("composite_signal_score")),
                    "event_type": event_type,
                    "emit_signal": True,
                    "m3_payload": m3_payload,
                    **shared_m3,
                },
            )
        )
    payloads.append(
        (
            "m3",
            {
                "floor_value": _to_optional_float(m3_payload.get("floor_m3")),
                "ceiling_value": None,
                "floor_time_bucket": _time_text(m3_payload.get("floor_week_m3")),
                "ceiling_time_bucket": "",
                "floor_time_probability": _to_float(m3_payload.get("floor_week_m3_confidence"), 0.0),
                "ceiling_time_probability": 0.0,
                "confidence_score": _to_float(m3_payload.get("floor_week_m3_confidence"), 0.0),
                "expected_return": _to_optional_float(m3_payload.get("expected_return_m3")),
                "expected_range": _to_optional_float(m3_payload.get("expected_range_m3")),
                "event_type": event_type,
                "emit_signal": False,
                "m3_payload": m3_payload,
                **shared_m3,
            },
        )
    )
    return payloads


def build_prediction_record(
    symbol: str,
    as_of: datetime,
    horizon: Literal["d1", "w1", "q1", "m3"],
    payload: dict,
    model_version: str,
) -> PredictionRecord:
    return PredictionRecord(
        symbol=symbol,
        as_of=as_of,
        event_type=payload["event_type"],
        horizon=horizon,
        floor_value=payload["floor_value"],
        ceiling_value=payload["ceiling_value"],
        floor_time_bucket=payload["floor_time_bucket"],
        ceiling_time_bucket=payload["ceiling_time_bucket"],
        floor_time_probability=payload["floor_time_probability"],
        ceiling_time_probability=payload["ceiling_time_probability"],
        confidence_score=payload["confidence_score"],
        expected_return=payload["expected_return"],
        expected_range=payload["expected_range"],
        m3_payload=payload["m3_payload"],
        floor_m3=payload.get("floor_m3"),
        floor_week_m3=payload.get("floor_week_m3"),
        floor_week_m3_confidence=payload.get("floor_week_m3_confidence"),
        floor_week_m3_top3=payload.get("floor_week_m3_top3", []),
        floor_week_m3_start_date=payload.get("floor_week_m3_start_date"),
        floor_week_m3_end_date=payload.get("floor_week_m3_end_date"),
        floor_week_m3_label_human=payload.get("floor_week_m3_label_human"),
        m3_status=payload.get("m3_status"),
        m3_block_reason=payload.get("m3_block_reason"),
        model_version=model_version,
    )
