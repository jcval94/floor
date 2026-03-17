from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from features.build_training_from_db import build_rows_from_db
from features.feature_builder import build_features
from floor.config import RuntimeConfig
from floor.external.google_sheets import fetch_recommendations
from floor.prediction_reconciliation import reconcile_predictions
from floor.schemas import OrderRecord, PredictionRecord, SignalRecord
from floor.storage import append_jsonl
from forecasting.run_forecast import run_forecast_pipeline

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")

MIN_SIGNAL_CONFIDENCE = 0.55
EXPECTED_RETURN_THRESHOLD = 0.01


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _signal_from_prediction(
    symbol: str,
    horizon: Literal["d1", "w1", "q1", "m3"],
    floor: float,
    ceiling: float,
    expected_return: float | None,
    confidence_score: float | None,
    composite_signal_score: float | None,
) -> SignalRecord:
    spread = max(ceiling - floor, 0.01)
    spread_confidence = min(0.95, max(0.5, spread / max(floor, 1)))
    confidence = max(
        min(_to_float(confidence_score, 0.0), 1.0),
        min(_to_float(composite_signal_score, 0.0), 1.0),
        spread_confidence,
    )
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
            "Expected return + confidence decision "
            f"(expected_return={expected_ret:.4f}, confidence={confidence:.4f}, "
            f"threshold={EXPECTED_RETURN_THRESHOLD:.4f}, min_confidence={MIN_SIGNAL_CONFIDENCE:.2f}); "
            f"spread={spread:.4f} as auxiliary quality metric"
        ),
    )


def maybe_build_order(signal: SignalRecord, cfg: RuntimeConfig) -> OrderRecord | None:
    if signal.action == "HOLD":
        return None
    mode: Literal["PAPER", "LIVE"] = "LIVE" if cfg.live_trading_enabled else "PAPER"
    if mode == "LIVE" and not cfg.live_trading_enabled:
        return None
    return OrderRecord(
        symbol=signal.symbol,
        as_of=signal.as_of,
        action=signal.action,
        qty=1,
        order_type="MKT",
        mode=mode,
    )


def _latest_feature_rows(cfg: RuntimeConfig, symbols: list[str]) -> list[dict]:
    symbol_set = {s.upper() for s in symbols}
    raw_rows = build_rows_from_db(
        db_path=cfg.data_dir / "market" / "market_data.sqlite",
        universe_path=cfg.root_dir / "config" / "universe.yaml",
    )
    selected = [row for row in raw_rows if str(row.get("symbol", "")).upper() in symbol_set]
    featured = build_features(selected)

    latest_by_symbol: dict[str, dict] = {}
    for row in featured:
        latest_by_symbol[str(row["symbol"]).upper()] = row

    missing_symbols = [symbol for symbol in symbols if symbol.upper() not in latest_by_symbol]
    logger.info(
        "[predictions] feature snapshot raw_rows=%s selected_rows=%s featured_rows=%s symbols_requested=%s symbols_missing=%s",
        len(raw_rows),
        len(selected),
        len(featured),
        len(symbols),
        len(missing_symbols),
    )
    if missing_symbols:
        logger.warning("[predictions] missing latest feature rows symbols=%s", ",".join(missing_symbols[:20]))

    return [latest_by_symbol[symbol.upper()] for symbol in symbols if symbol.upper() in latest_by_symbol]


def _validate_feature_rows(feature_rows: list[dict]) -> None:
    logger.info("[predictions][validate] START feature rows validation rows=%s", len(feature_rows))
    if not feature_rows:
        raise RuntimeError("Feature rows validation failed: empty input")

    stale = 0
    malformed = 0
    for row in feature_rows:
        ts = row.get("timestamp")
        if not isinstance(ts, str) or not ts:
            malformed += 1
            continue
        try:
            parsed = datetime.fromisoformat(ts)
        except ValueError:
            malformed += 1
            continue
        age_days = (datetime.now(tz=ET).date() - parsed.date()).days
        if age_days > 7:
            stale += 1

    if malformed:
        raise RuntimeError(f"Feature rows validation failed: malformed timestamps={malformed}")

    if stale:
        logger.warning(
            "[predictions][validate] feature recency warning stale_rows=%s/%s threshold_days=7",
            stale,
            len(feature_rows),
        )
    logger.info("[predictions][validate] DONE feature rows validation")


def _validate_prediction_payload(symbol: str, horizon: str, payload: dict) -> None:
    floor_value = payload.get("floor_value")
    ceiling_value = payload.get("ceiling_value")
    confidence = _to_float(payload.get("confidence_score"), 0.0)
    expected_range = payload.get("expected_range")

    if floor_value is None and horizon != "m3":
        raise RuntimeError(f"Prediction payload invalid symbol={symbol} horizon={horizon}: floor_value is missing")
    if ceiling_value is None and horizon in {"d1", "w1", "q1"}:
        raise RuntimeError(f"Prediction payload invalid symbol={symbol} horizon={horizon}: ceiling_value is missing")
    if floor_value is not None and ceiling_value is not None and float(floor_value) > float(ceiling_value):
        raise RuntimeError(
            f"Prediction payload invalid symbol={symbol} horizon={horizon}: floor_value > ceiling_value ({floor_value}>{ceiling_value})"
        )
    if not 0.0 <= confidence <= 1.0:
        raise RuntimeError(
            f"Prediction payload invalid symbol={symbol} horizon={horizon}: confidence_score out of range ({confidence})"
        )
    if expected_range is not None and float(expected_range) < 0:
        raise RuntimeError(
            f"Prediction payload invalid symbol={symbol} horizon={horizon}: expected_range is negative ({expected_range})"
        )


def _prediction_payloads(row: dict, event_type: str) -> list[tuple[Literal["d1", "w1", "q1", "m3"], dict]]:
    """Build normalized prediction payloads for d1/w1/q1/m3 horizons."""
    confidence = _to_float(row.get("confidence_score", 0.5), 0.5)
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

    # Flatten m3 contract fields in every horizon row so they survive sqlite/jsonl/dashboard snapshots.
    shared_m3_fields = {
        "floor_m3": m3_payload.get("floor_m3"),
        "floor_week_m3": m3_payload.get("floor_week_m3"),
        "floor_week_m3_confidence": m3_payload.get("floor_week_m3_confidence"),
        "floor_week_m3_top3": m3_payload.get("floor_week_m3_top3", []),
        "floor_week_m3_start_date": m3_payload.get("floor_week_m3_start_date"),
        "floor_week_m3_end_date": m3_payload.get("floor_week_m3_end_date"),
        "floor_week_m3_label_human": m3_payload.get("floor_week_m3_label_human"),
        "expected_return_m3": m3_payload.get("expected_return_m3"),
        "expected_range_m3": m3_payload.get("expected_range_m3"),
        "m3_status": m3_payload.get("m3_status"),
        "m3_block_reason": m3_payload.get("m3_block_reason"),
    }

    payloads: list[tuple[Literal["d1", "w1", "q1", "m3"], dict]] = [
        (
            "d1",
            {
                "floor_value": _to_float(row.get("floor_d1")),
                "ceiling_value": _to_float(row.get("ceiling_d1")),
                "floor_time_bucket": str(row["floor_time_bucket_d1"]),
                "ceiling_time_bucket": str(row["ceiling_time_bucket_d1"]),
                "floor_time_probability": confidence,
                "ceiling_time_probability": confidence,
                "confidence_score": confidence,
                "expected_return": _to_float(row.get("expected_return_d1", 0.0), 0.0),
                "expected_range": _to_float(row.get("expected_range_d1", 0.0), 0.0),
                "composite_signal_score": row.get("composite_signal_score_d1", row.get("composite_signal_score")),
                "event_type": event_type,
                "emit_signal": True,
                "m3_payload": m3_payload,
                **shared_m3_fields,
            },
        ),
        (
            "w1",
            {
                "floor_value": _to_float(row.get("floor_w1")),
                "ceiling_value": _to_float(row.get("ceiling_w1")),
                "floor_time_bucket": str(row["floor_day_w1"]),
                "ceiling_time_bucket": str(row["ceiling_day_w1"]),
                "floor_time_probability": confidence,
                "ceiling_time_probability": confidence,
                "confidence_score": confidence,
                "expected_return": _to_float(row.get("expected_return_w1", 0.0), 0.0),
                "expected_range": _to_float(row.get("expected_range_w1", 0.0), 0.0),
                "composite_signal_score": row.get("composite_signal_score_w1", row.get("composite_signal_score")),
                "event_type": event_type,
                "emit_signal": True,
                "m3_payload": m3_payload,
                **shared_m3_fields,
            },
        ),
        (
            "q1",
            {
                "floor_value": _to_float(row.get("floor_q1")),
                "ceiling_value": _to_float(row.get("ceiling_q1")),
                "floor_time_bucket": str(row["floor_day_q1"]),
                "ceiling_time_bucket": str(row["ceiling_day_q1"]),
                "floor_time_probability": confidence,
                "ceiling_time_probability": confidence,
                "confidence_score": confidence,
                "expected_return": _to_float(row.get("expected_return_q1", 0.0), 0.0),
                "expected_range": _to_float(row.get("expected_range_q1", 0.0), 0.0),
                "composite_signal_score": row.get("composite_signal_score_q1", row.get("composite_signal_score")),
                "event_type": event_type,
                "emit_signal": True,
                "m3_payload": m3_payload,
                **shared_m3_fields,
            },
        ),
    ]

    payloads.append(
        (
            "m3",
            {
                "floor_value": _to_optional_float(m3_payload.get("floor_m3")),
                "ceiling_value": None,
                "floor_time_bucket": str(m3_payload.get("floor_week_m3") or ""),
                "ceiling_time_bucket": "",
                "floor_time_probability": _to_float(m3_payload.get("floor_week_m3_confidence"), 0.0),
                "ceiling_time_probability": 0.0,
                "confidence_score": _to_float(m3_payload.get("floor_week_m3_confidence"), 0.0),
                "expected_return": _to_optional_float(m3_payload.get("expected_return_m3")),
                "expected_range": _to_optional_float(m3_payload.get("expected_range_m3")),
                "event_type": event_type,
                "emit_signal": False,
                "m3_payload": m3_payload,
                **shared_m3_fields,
            },
        )
    )

    return payloads


def run_intraday_cycle(
    event_type: Literal["OPEN", "OPEN_PLUS_2H", "OPEN_PLUS_4H", "OPEN_PLUS_6H", "CLOSE"],
    symbols: list[str],
    cfg: RuntimeConfig,
) -> None:
    logger.info("[predictions] STEP 1/7 start intraday cycle event=%s symbols=%s", event_type, len(symbols))
    market_rows = _latest_feature_rows(cfg, symbols)
    if not market_rows:
        raise RuntimeError("No latest feature rows available in market_data.sqlite for requested symbols")
    _validate_feature_rows(market_rows)
    logger.info("[predictions] STEP 1/7 DONE feature load + validation rows=%s", len(market_rows))

    try:
        external = {record.symbol: record for record in fetch_recommendations(cfg.recommendations_csv_url)}
        logger.info("[predictions] loaded external recommendations count=%s", len(external))
    except Exception as exc:
        logger.exception("[predictions] failed to fetch external recommendations: %s", exc)
        external = {}
    logger.info("[predictions] STEP 2/7 DONE external recommendations count=%s", len(external))

    ai_by_symbol = {
        symbol: {
            "ai_action": record.action,
            "ai_conviction": record.confidence,
            "ai_consensus_score": record.confidence,
            "ai_note": record.note,
        }
        for symbol, record in external.items()
    }

    as_of = datetime.now(tz=ET)
    generated = run_forecast_pipeline(
        market_rows=market_rows,
        ai_by_symbol=ai_by_symbol,
        session=event_type,
        as_of=as_of,
        model_registry_dir=cfg.data_dir / "training" / "models",
    )
    forecasts = generated["dataset_forecasts"]
    blocked = generated["blocked_list"]
    logger.info(
        "[predictions] STEP 3/7 DONE forecast pipeline forecasts=%s blocked=%s",
        len(forecasts),
        len(blocked),
    )
    if blocked:
        logger.warning(
            "[predictions] blocked forecasts count=%s sample=%s",
            len(blocked),
            blocked[:3],
        )
    if not forecasts:
        reason = "; ".join(str(item.get("reason", "unknown")) for item in blocked[:3]) or "forecast generation blocked"
        raise RuntimeError(reason)

    for row in forecasts:
        symbol = str(row["symbol"]).upper()
        logger.info("[predictions] processing symbol=%s model=%s", symbol, row.get("model_version"))
        for horizon, payload in _prediction_payloads(row, event_type):
            _validate_prediction_payload(symbol, horizon, payload)
            prediction = PredictionRecord(
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
                model_version=str(row.get("model_version", "unknown")),
            )
            append_jsonl(cfg.data_dir / "predictions" / f"{symbol}.jsonl", prediction)
            logger.info("[predictions] wrote prediction symbol=%s horizon=%s", symbol, horizon)

            if payload.get("emit_signal", True):
                signal = _signal_from_prediction(
                    symbol,
                    horizon,
                    float(prediction.floor_value or 0.0),
                    float(prediction.ceiling_value or 0.0),
                    prediction.expected_return,
                    prediction.confidence_score,
                    payload.get("composite_signal_score"),
                )
                if symbol in external and external[symbol].action in {"BUY", "SELL", "HOLD"}:
                    previous_action = signal.action
                    signal.action = external[symbol].action  # type: ignore[assignment]
                    signal.rationale += (
                        " | external_override="
                        f"{external[symbol].action} (model_action={previous_action}, note={external[symbol].note})"
                    )
                append_jsonl(cfg.data_dir / "signals" / f"{symbol}.jsonl", signal)
                logger.info("[predictions] wrote signal symbol=%s horizon=%s action=%s", symbol, horizon, signal.action)

                order = maybe_build_order(signal, cfg)
                if order:
                    append_jsonl(cfg.data_dir / "orders" / f"{symbol}.jsonl", order)
                    logger.info("[predictions] wrote order symbol=%s horizon=%s", symbol, horizon)
    logger.info("[predictions] STEP 4/7 DONE payload validation + persistence")

    reconciliation = reconcile_predictions(cfg.data_dir)
    logger.info("[predictions] STEP 5/7 DONE reconciliation run")
    logger.info(
        "[predictions] reconciliation pending=%s reconciled=%s skipped=%s",
        reconciliation.get("pending", 0),
        reconciliation.get("reconciled", 0),
        reconciliation.get("skipped", 0),
    )

    logger.info("[predictions] finished intraday cycle event=%s forecasts=%s blocked=%s", event_type, len(forecasts), len(blocked))
    logger.info("[predictions] STEP 6/7 DONE cycle bookkeeping")
    logger.info("[predictions] STEP 7/7 DONE intraday cycle successful")
