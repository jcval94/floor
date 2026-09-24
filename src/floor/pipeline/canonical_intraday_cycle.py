from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from floor.config import RuntimeConfig
from floor.pipeline.prediction_runtime import (
    _latest_feature_rows,
    _log_model_registry_preflight,
    _model_input_snapshot,
    _model_output_snapshot,
    _prediction_payloads,
    _validate_feature_rows,
    _validate_prediction_payload,
    build_prediction_record,
)
from floor.persistence_db import PersistenceWriter
from floor.schemas import SignalRecord
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
    observed_symbols = [
        str(row.get("symbol") or "").strip().upper() for row in forecasts
    ]
    counts = Counter(observed_symbols)
    observed = {symbol for symbol in observed_symbols if symbol}
    missing = sorted(expected - observed)
    extra = sorted(observed - expected)
    duplicates = sorted(
        symbol for symbol, count in counts.items() if symbol and count != 1
    )

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
            "Canonical intraday cycle refused incomplete forecast batch: "
            + "; ".join(problems)
        )


def _batch_id(as_of: datetime, event_type: EventType) -> str:
    """Stable logical checkpoint identity used across retries."""

    return f"{as_of.astimezone(ET).date().isoformat()}:{event_type}"


def _input_snapshot_id(market_rows: list[dict], forecasts: list[dict]) -> str:
    """Identify the exact model input state independently of checkpoint time.

    Floor currently serves daily-bar features. Multiple intraday checkpoints can
    therefore see identical inputs. The fingerprint deliberately excludes
    checkpoint/event timestamps and includes model versions so only genuinely
    new market/model state can create new evidence.
    """

    model_versions = sorted(
        {
            str(row.get("model_version") or "unknown")
            for row in forecasts
        }
    )
    normalized_rows = [
        {
            "symbol": str(row.get("symbol") or "").strip().upper(),
            "inputs": _model_input_snapshot(row, None),
        }
        for row in sorted(
            market_rows,
            key=lambda item: str(item.get("symbol") or "").strip().upper(),
        )
    ]
    raw = json.dumps(
        {
            "model_versions": model_versions,
            "rows": normalized_rows,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _input_snapshot_marker(data_dir: Path, snapshot_id: str) -> Path:
    return data_dir / "snapshots" / "input_snapshots" / f"{snapshot_id}.json"


def _write_input_snapshot_marker(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def run_intraday_cycle(
    event_type: EventType,
    symbols: list[str],
    cfg: RuntimeConfig,
    *,
    as_of: datetime | None = None,
    market_session: date | None = None,
) -> dict[str, object]:
    """Canonical range-forecast cycle. No external override or directional orders."""

    market_rows = _latest_feature_rows(cfg, symbols, max_market_session=market_session)
    if len(market_rows) != len(symbols):
        observed = {
            str(row.get("symbol") or "").strip().upper() for row in market_rows
        }
        missing = sorted({symbol.upper() for symbol in symbols} - observed)
        raise RuntimeError(
            "Canonical intraday cycle refused incomplete feature batch: missing="
            + ",".join(missing)
        )
    _validate_feature_rows(market_rows)

    as_of = as_of or datetime.now(tz=ET)
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=ET)
    as_of = as_of.astimezone(ET)
    batch_id = _batch_id(as_of, event_type)
    logger.info(
        "[canonical-intraday] start event=%s batch_id=%s symbols=%s "
        "directional_signals=disabled order_generation=disabled",
        event_type,
        batch_id,
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

    input_snapshot_id = _input_snapshot_id(market_rows, forecasts)
    marker_path = _input_snapshot_marker(cfg.data_dir, input_snapshot_id)
    if marker_path.exists():
        logger.info(
            "[canonical-intraday] no new market/model input; suppressing duplicate evidence "
            "event=%s batch_id=%s input_snapshot_id=%s reconciliation=deferred_to_eod",
            event_type,
            batch_id,
            input_snapshot_id,
        )
        return {
            "status": "NO_NEW_INPUT",
            "batch_id": batch_id,
            "input_snapshot_id": input_snapshot_id,
            "forecasts": len(forecasts),
            "reconciliation": {"status": "DEFERRED_TO_EOD"},
        }

    # The entire forecast batch shares one schema initialization, connection
    # and transaction. The input marker is emitted only after this commits.
    with PersistenceWriter(cfg.data_dir / "persistence" / "app.sqlite") as writer:
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
                append_jsonl(
                    cfg.data_dir / "predictions" / f"{symbol}.jsonl",
                    prediction,
                    batch_id=batch_id,
                    writer=writer,
                )

                if payload.get("emit_signal", True):
                    # Range models do not estimate directional alpha. Persist an
                    # explicit HOLD readiness record rather than manufacturing BUY/SELL.
                    signal = SignalRecord(
                        symbol=symbol,
                        as_of=as_of,
                        horizon=horizon,
                        action="HOLD",
                        confidence=round(float(prediction.confidence_score or 0.0), 4),
                        rationale=(
                            "Directional alpha unavailable; HOLD emitted. "
                            "Confidence describes validation interval coverage only."
                        ),
                    )
                    append_jsonl(
                        cfg.data_dir / "signals" / f"{symbol}.jsonl",
                        signal,
                        batch_id=batch_id,
                    writer=writer,
                    )

    _write_input_snapshot_marker(
        marker_path,
        {
            "schema_version": 1,
            "input_snapshot_id": input_snapshot_id,
            "first_batch_id": batch_id,
            "first_event": event_type,
            "first_as_of": as_of.isoformat(),
            "market_session": market_session.isoformat() if market_session else None,
            "symbols": sorted({str(row.get("symbol") or "").upper() for row in forecasts}),
            "model_versions": sorted(
                {str(row.get("model_version") or "unknown") for row in forecasts}
            ),
        },
    )

    logger.info(
        "[canonical-intraday] complete event=%s batch_id=%s input_snapshot_id=%s "
        "forecasts=%s reconciliation=deferred_to_eod",
        event_type,
        batch_id,
        input_snapshot_id,
        len(forecasts),
    )
    return {
        "status": "WRITTEN",
        "batch_id": batch_id,
        "input_snapshot_id": input_snapshot_id,
        "forecasts": len(forecasts),
        "reconciliation": {"status": "DEFERRED_TO_EOD"},
    }
