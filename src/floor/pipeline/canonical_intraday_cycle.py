from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from floor.config import RuntimeConfig
from floor.pipeline.prediction_runtime import (
    _latest_feature_rows,
    _log_model_registry_preflight,
    _model_input_snapshot,
    _model_output_snapshot,
    _prediction_payloads,
    _signal_from_prediction,
    _validate_feature_rows,
    _validate_prediction_payload,
    build_prediction_record,
)
from floor.prediction_reconciliation import reconcile_predictions
from floor.storage import append_jsonl
from forecasting.run_forecast import run_forecast_pipeline

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")

EventType = Literal["OPEN", "OPEN_PLUS_2H", "OPEN_PLUS_4H", "OPEN_PLUS_6H", "CLOSE"]


def _validate_forecast_batch(
    forecasts: list[dict],
    blocked: list[dict],
    expected_symbols: list[str],
) -> None:
    """Refuse partial, duplicate, extra, or synthetically substituted forecast batches."""
    expected = {symbol.strip().upper() for symbol in expected_symbols if symbol.strip()}
    observed_symbols = [str(row.get("symbol") or "").strip().upper() for row in forecasts]
    counts = Counter(observed_symbols)
    observed = {symbol for symbol in observed_symbols if symbol}
    missing = sorted(expected - observed)
    extra = sorted(observed - expected)
    duplicates = sorted(symbol for symbol, count in counts.items() if symbol and count != 1)

    problems: list[str] = []
    if blocked:
        blocked_symbols = sorted(
            {
                str(item.get("symbol") or "").strip().upper()
                for item in blocked
                if str(item.get("symbol") or "").strip()
            }
        )
        problems.append("blocked=" + ",".join(blocked_symbols[:20] or ["unknown"]))
    if missing:
        problems.append("missing=" + ",".join(missing[:20]))
    if extra:
        problems.append("extra=" + ",".join(extra[:20]))
    if duplicates:
        problems.append("duplicates=" + ",".join(duplicates[:20]))
    if problems:
        raise RuntimeError(
            "Canonical intraday cycle refused incomplete forecast batch: " + "; ".join(problems)
        )


def run_intraday_cycle(
    event_type: EventType,
    symbols: list[str],
    cfg: RuntimeConfig,
) -> None:
    """Canonical prediction/signal cycle. No external override and no order creation."""
    market_rows = _latest_feature_rows(cfg, symbols)
    if len(market_rows) != len(symbols):
        observed = {str(row.get("symbol") or "").strip().upper() for row in market_rows}
        missing = sorted({symbol.upper() for symbol in symbols} - observed)
        raise RuntimeError(
            "Canonical intraday cycle refused incomplete feature batch: missing="
            + ",".join(missing)
        )
    _validate_feature_rows(market_rows)

    as_of = datetime.now(tz=ET)
    logger.info(
        "[canonical-intraday] start event=%s symbols=%s external_recommendations=disabled order_generation=disabled",
        event_type,
        len(symbols),
    )
    for row in market_rows:
        symbol = str(row.get("symbol", "")).upper()
        logger.info(
            "[canonical-intraday][model-io] INPUT symbol=%s values=%s",
            symbol,
            _model_input_snapshot(row, None),
        )

    _log_model_registry_preflight(cfg)
    generated = run_forecast_pipeline(
        market_rows=market_rows,
        ai_by_symbol={},
        session=event_type,
        as_of=as_of,
        model_registry_dir=cfg.data_dir / "training" / "models",
    )
    forecasts = list(generated.get("dataset_forecasts", []))
    blocked = list(generated.get("blocked_list", []))
    _validate_forecast_batch(forecasts, blocked, symbols)

    for row in forecasts:
        symbol = str(row["symbol"]).upper()
        logger.info(
            "[canonical-intraday][model-io] OUTPUT symbol=%s values=%s",
            symbol,
            _model_output_snapshot(row),
        )
        for horizon, payload in _prediction_payloads(row, event_type):
            _validate_prediction_payload(symbol, horizon, payload)
            prediction = build_prediction_record(
                symbol=symbol,
                as_of=as_of,
                horizon=horizon,
                payload=payload,
                model_version=str(row.get("model_version", "unknown")),
            )
            append_jsonl(cfg.data_dir / "predictions" / f"{symbol}.jsonl", prediction)

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
                append_jsonl(cfg.data_dir / "signals" / f"{symbol}.jsonl", signal)

    reconciliation = reconcile_predictions(cfg.data_dir)
    logger.info(
        "[canonical-intraday] complete event=%s forecasts=%s reconciliation=%s",
        event_type,
        len(forecasts),
        reconciliation,
    )
