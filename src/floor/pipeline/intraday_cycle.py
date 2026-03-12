from __future__ import annotations

from datetime import datetime
from typing import Literal, cast
from zoneinfo import ZoneInfo

from floor.config import RuntimeConfig
from floor.external.google_sheets import fetch_recommendations
from floor.modeling.contracts import ChampionModel
from floor.schemas import OrderRecord, PredictionRecord, SignalRecord
from floor.storage import append_jsonl

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
    model = ChampionModel()
    external = {r.symbol: r for r in fetch_recommendations(cfg.recommendations_csv_url)}

    for symbol in symbols:
        for horizon in ["d1", "w1", "q1"]:
            horizon_literal = cast(Literal["d1", "w1", "q1"], horizon)
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

            signal = _signal_from_prediction(symbol, horizon_literal, pred.floor_value, pred.ceiling_value)
            if symbol in external and external[symbol].action in {"BUY", "SELL", "HOLD"}:
                signal.action = cast(Literal["BUY", "SELL", "HOLD"], external[symbol].action)
                signal.rationale += f" | external={external[symbol].note}"
            append_jsonl(cfg.data_dir / "signals" / f"{symbol}.jsonl", signal)

            order = maybe_build_order(signal, cfg)
            if order:
                append_jsonl(cfg.data_dir / "orders" / f"{symbol}.jsonl", order)
