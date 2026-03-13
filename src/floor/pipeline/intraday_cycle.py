from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from features.build_training_from_db import build_rows_from_db
from features.feature_builder import build_features
from floor.config import RuntimeConfig
from floor.external.google_sheets import fetch_recommendations
from floor.schemas import OrderRecord, PredictionRecord, SignalRecord
from floor.storage import append_jsonl
from forecasting.run_forecast import run_forecast_pipeline

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")


def _signal_from_prediction(symbol: str, horizon: Literal["d1", "w1", "q1"], floor: float, ceiling: float) -> SignalRecord:
    spread = max(ceiling - floor, 0.01)
    confidence = min(0.95, max(0.5, spread / max(floor, 1)))
    action: Literal["BUY", "SELL", "HOLD"] = "HOLD"
    if confidence > 0.55:
        action = "BUY"
    return SignalRecord(
        symbol=symbol,
        as_of=datetime.now(tz=ET),
        horizon=horizon,
        action=action,
        confidence=round(confidence, 4),
        rationale="Quantile spread + calibrated temporal confidence",
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
    raw_rows = build_rows_from_db(
        db_path=cfg.data_dir / "market" / "market_data.sqlite",
        universe_path=cfg.root_dir / "config" / "universe.yaml",
    )
    selected = [row for row in raw_rows if str(row.get("symbol", "")).upper() in set(symbols)]
    featured = build_features(selected)

    latest_by_symbol: dict[str, dict] = {}
    for row in featured:
        latest_by_symbol[str(row["symbol"]).upper()] = row
    return [latest_by_symbol[symbol] for symbol in symbols if symbol in latest_by_symbol]


def _prediction_payloads(row: dict, event_type: str) -> list[tuple[Literal["d1", "w1", "q1"], dict]]:
    confidence = float(row.get("confidence_score", 0.5) or 0.5)
    return [
        (
            "d1",
            {
                "floor_value": float(row["floor_d1"]),
                "ceiling_value": float(row["ceiling_d1"]),
                "floor_time_bucket": str(row["floor_time_bucket_d1"]),
                "ceiling_time_bucket": str(row["ceiling_time_bucket_d1"]),
                "floor_time_probability": confidence,
                "ceiling_time_probability": confidence,
                "event_type": event_type,
            },
        ),
        (
            "w1",
            {
                "floor_value": float(row["floor_w1"]),
                "ceiling_value": float(row["ceiling_w1"]),
                "floor_time_bucket": str(row["floor_day_w1"]),
                "ceiling_time_bucket": str(row["ceiling_day_w1"]),
                "floor_time_probability": confidence,
                "ceiling_time_probability": confidence,
                "event_type": event_type,
            },
        ),
        (
            "q1",
            {
                "floor_value": float(row["floor_q1"]),
                "ceiling_value": float(row["ceiling_q1"]),
                "floor_time_bucket": str(row["floor_day_q1"]),
                "ceiling_time_bucket": str(row["ceiling_day_q1"]),
                "floor_time_probability": confidence,
                "ceiling_time_probability": confidence,
                "event_type": event_type,
            },
        ),
    ]


def run_intraday_cycle(
    event_type: Literal["OPEN", "OPEN_PLUS_2H", "OPEN_PLUS_4H", "OPEN_PLUS_6H", "CLOSE"],
    symbols: list[str],
    cfg: RuntimeConfig,
) -> None:
    logger.info("[predictions] start intraday cycle event=%s symbols=%s", event_type, len(symbols))
    market_rows = _latest_feature_rows(cfg, symbols)
    if not market_rows:
        raise RuntimeError("No latest feature rows available in market_data.sqlite for requested symbols")

    try:
        external = {record.symbol: record for record in fetch_recommendations(cfg.recommendations_csv_url)}
        logger.info("[predictions] loaded external recommendations count=%s", len(external))
    except Exception as exc:
        logger.exception("[predictions] failed to fetch external recommendations: %s", exc)
        external = {}

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
    generated = run_forecast_pipeline(market_rows=market_rows, ai_by_symbol=ai_by_symbol, session=event_type, as_of=as_of)
    forecasts = generated["dataset_forecasts"]
    blocked = generated["blocked_list"]
    if not forecasts:
        reason = "; ".join(str(item.get("reason", "unknown")) for item in blocked[:3]) or "forecast generation blocked"
        raise RuntimeError(reason)

    for row in forecasts:
        symbol = str(row["symbol"]).upper()
        logger.info("[predictions] processing symbol=%s model=%s", symbol, row.get("model_version"))
        for horizon, payload in _prediction_payloads(row, event_type):
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
                model_version=str(row.get("model_version", "unknown")),
            )
            append_jsonl(cfg.data_dir / "predictions" / f"{symbol}.jsonl", prediction)
            logger.info("[predictions] wrote prediction symbol=%s horizon=%s", symbol, horizon)

            signal = _signal_from_prediction(symbol, horizon, prediction.floor_value, prediction.ceiling_value)
            if symbol in external and external[symbol].action in {"BUY", "SELL", "HOLD"}:
                signal.action = external[symbol].action  # type: ignore[assignment]
                signal.rationale += f" | external={external[symbol].note}"
            append_jsonl(cfg.data_dir / "signals" / f"{symbol}.jsonl", signal)
            logger.info("[predictions] wrote signal symbol=%s horizon=%s action=%s", symbol, horizon, signal.action)

            order = maybe_build_order(signal, cfg)
            if order:
                append_jsonl(cfg.data_dir / "orders" / f"{symbol}.jsonl", order)
                logger.info("[predictions] wrote order symbol=%s horizon=%s", symbol, horizon)

    logger.info("[predictions] finished intraday cycle event=%s forecasts=%s blocked=%s", event_type, len(forecasts), len(blocked))
