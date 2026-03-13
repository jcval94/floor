from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal, cast
from zoneinfo import ZoneInfo

from floor.config import RuntimeConfig
from floor.external.google_sheets import fetch_recommendations
from floor.modeling.contracts import ChampionModel
from floor.schemas import OrderRecord, PredictionRecord, SignalRecord
from floor.storage import append_jsonl

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


def run_intraday_cycle(
    event_type: Literal["OPEN", "OPEN_PLUS_2H", "OPEN_PLUS_4H", "OPEN_PLUS_6H", "CLOSE"],
    symbols: list[str],
    cfg: RuntimeConfig,
) -> None:
    logger.info("[predictions] start intraday cycle event=%s symbols=%s", event_type, len(symbols))
    model = ChampionModel()
    try:
        external = {r.symbol: r for r in fetch_recommendations(cfg.recommendations_csv_url)}
        logger.info("[predictions] loaded external recommendations count=%s", len(external))
    except Exception as exc:
        logger.exception("[predictions] failed to fetch external recommendations: %s", exc)
        external = {}

    for symbol in symbols:
        logger.info("[predictions] processing symbol=%s", symbol)
        for horizon in ["d1", "w1", "q1"]:
            horizon_literal = cast(Literal["d1", "w1", "q1"], horizon)
            try:
                pred = model.predict(symbol=symbol, horizon=horizon_literal, event_type=event_type)
                prediction = PredictionRecord(
                    symbol=symbol,
                    as_of=datetime.now(tz=ET),
                    event_type=event_type,
                    horizon=horizon_literal,
                    floor_value=pred.floor_value,
                    ceiling_value=pred.ceiling_value,
                    floor_time_bucket=pred.floor_bucket,
                    ceiling_time_bucket=pred.ceiling_bucket,
                    floor_time_probability=pred.floor_bucket_prob,
                    ceiling_time_probability=pred.ceiling_bucket_prob,
                    model_version=model.version,
                )
                append_jsonl(cfg.data_dir / "predictions" / f"{symbol}.jsonl", prediction)
                logger.info("[predictions] wrote prediction symbol=%s horizon=%s", symbol, horizon_literal)
                logger.info("[predictions] prediction sample=%s", prediction)
            except Exception as exc:
                logger.exception(
                    "[predictions] failed prediction symbol=%s horizon=%s error=%s", symbol, horizon_literal, exc
                )
                continue

            try:
                signal = _signal_from_prediction(symbol, horizon_literal, pred.floor_value, pred.ceiling_value)
                if symbol in external and external[symbol].action in {"BUY", "SELL", "HOLD"}:
                    signal.action = cast(Literal["BUY", "SELL", "HOLD"], external[symbol].action)
                    signal.rationale += f" | external={external[symbol].note}"
                append_jsonl(cfg.data_dir / "signals" / f"{symbol}.jsonl", signal)
                logger.info("[predictions] wrote signal symbol=%s horizon=%s action=%s", symbol, horizon_literal, signal.action)
            except Exception as exc:
                logger.exception("[predictions] failed signal symbol=%s horizon=%s error=%s", symbol, horizon_literal, exc)
                continue

            try:
                order = maybe_build_order(signal, cfg)
                if order:
                    append_jsonl(cfg.data_dir / "orders" / f"{symbol}.jsonl", order)
                    logger.info("[predictions] wrote order symbol=%s horizon=%s", symbol, horizon_literal)
            except Exception as exc:
                logger.exception("[predictions] failed order symbol=%s horizon=%s error=%s", symbol, horizon_literal, exc)
    logger.info("[predictions] finished intraday cycle event=%s", event_type)
