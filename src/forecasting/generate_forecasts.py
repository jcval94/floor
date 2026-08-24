from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from forecasting.merge_ai_signal import merge_market_with_ai_signal
from forecasting.parity_models import (
    DEFAULT_M3_TIMING_ABSTENTION_THRESHOLD,
    load_champion_models,
)
from forecasting.render_time_labels import render_horizon_time_labels

logger = logging.getLogger(__name__)

REQUIRED_MARKET_COLUMNS = ["symbol", "close", "high", "low"]
REQUIRED_M3_COLUMNS = ["close", "atr_14", "trend_context_m3", "drawdown_13w"]


def _blocked_reason(row: dict) -> str | None:
    missing = [
        column
        for column in REQUIRED_MARKET_COLUMNS
        if row.get(column) in (None, "")
    ]
    return f"Missing market fields: {','.join(missing)}" if missing else None


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _m3_block_reason(row: dict) -> str | None:
    missing = [
        column for column in REQUIRED_M3_COLUMNS if row.get(column) in (None, "")
    ]
    return f"Missing m3 fields: {','.join(missing)}" if missing else None


def _model_confidence(*breach_probs: float) -> float:
    """Aggregate empirically calibrated non-breach probabilities.

    Individual horizon probabilities are validation-holdout rates. We keep the
    aggregate as a quality/readiness score only; it is not a probability of a
    BUY/SELL outcome.
    """

    values = [max(0.0, min(1.0, 1.0 - float(prob))) for prob in breach_probs]
    return sum(values) / len(values) if values else 0.0


def _midpoint_return(close: float, floor: float, ceiling: float) -> float:
    """Descriptive range midpoint displacement, explicitly not expected return."""

    return ((float(floor) + float(ceiling)) / 2.0 - close) / max(close, 1e-6)


def generate_forecasts(
    market_rows: list[dict],
    ai_by_symbol: dict[str, dict],
    session: str,
    as_of: datetime | None = None,
    model_registry_dir: Path | None = None,
) -> dict:
    as_of = as_of or datetime.now(tz=timezone.utc)
    model = (
        load_champion_models()
        if model_registry_dir is None
        else load_champion_models(model_registry_dir)
    )
    logger.info(
        "[forecasting] generating forecasts rows=%s session=%s "
        "model_available=%s model_version=%s",
        len(market_rows),
        session,
        model.is_available,
        model.version,
    )
    logger.info(
        "[forecasting][model-readout] selected_models=%s diagnostics=%s",
        model.model_readout,
        model.load_diagnostics,
    )

    forecasts: list[dict] = []
    blocked: list[dict] = []
    if not model.is_available:
        for raw in market_rows:
            blocked.append(
                {
                    "symbol": str(raw.get("symbol", "")).upper(),
                    "reason": (
                        "Pronóstico no disponible: faltan artefactos entrenados "
                        "(d1_champion.json, w1_champion.json, q1_champion.json, "
                        "value_champion.json y timing_champion.json)"
                    ),
                }
            )
        return {"forecasts": forecasts, "blocked": blocked}

    threshold = _safe_float(
        getattr(
            model,
            "m3_timing_abstention_threshold",
            DEFAULT_M3_TIMING_ABSTENTION_THRESHOLD,
        ),
        DEFAULT_M3_TIMING_ABSTENTION_THRESHOLD,
    )
    if not 0.0 <= threshold <= 1.0:
        threshold = DEFAULT_M3_TIMING_ABSTENTION_THRESHOLD

    for raw in market_rows:
        symbol = str(raw.get("symbol", "")).upper()
        reason = _blocked_reason(raw)
        if reason:
            blocked.append({"symbol": symbol, "reason": reason})
            continue

        try:
            row = merge_market_with_ai_signal(
                raw,
                ai_by_symbol.get(symbol),
                as_of=as_of,
            )
            d1 = model.predict_d1(row)
            w1 = model.predict_w1(row)
            q1 = model.predict_q1(row)
        except Exception as exc:
            logger.exception(
                "[forecasting] prediction failed symbol=%s error=%s",
                symbol,
                exc,
            )
            blocked.append({"symbol": symbol, "reason": f"Prediction failed: {exc}"})
            continue

        close = _safe_float(row.get("close"), 0.0)
        ai_eff = _safe_float(row.get("ai_effective_score"), 0.0)
        ai_weight = _safe_float(row.get("ai_weight"), 0.0)
        ai_present = bool(row.get("ai_present", False))
        expected_range_avg = (
            d1.expected_range + w1.expected_range + q1.expected_range
        ) / 3
        model_conf = _model_confidence(
            d1.breach_prob,
            w1.breach_prob,
            q1.breach_prob,
        )

        midpoint_d1 = _midpoint_return(close, d1.floor, d1.ceiling)
        midpoint_w1 = _midpoint_return(close, w1.floor, w1.ceiling)
        midpoint_q1 = _midpoint_return(close, q1.floor, q1.ceiling)

        out = {
            "symbol": symbol,
            "as_of": as_of.isoformat(),
            "session": session,
            "model_version": model.version,
            "floor_d1": d1.floor,
            "ceiling_d1": d1.ceiling,
            "floor_time_bucket_d1": d1.floor_time,
            "ceiling_time_bucket_d1": d1.ceiling_time,
            "breach_prob_d1": d1.breach_prob,
            "expected_return_d1": None,
            "range_midpoint_return_d1": round(midpoint_d1, 6),
            "expected_range_d1": d1.expected_range,
            "floor_w1": w1.floor,
            "ceiling_w1": w1.ceiling,
            "floor_day_w1": int(w1.floor_time),
            "ceiling_day_w1": int(w1.ceiling_time),
            "breach_prob_w1": w1.breach_prob,
            "expected_return_w1": None,
            "range_midpoint_return_w1": round(midpoint_w1, 6),
            "expected_range_w1": w1.expected_range,
            "floor_q1": q1.floor,
            "ceiling_q1": q1.ceiling,
            "floor_day_q1": int(q1.floor_time),
            "ceiling_day_q1": int(q1.ceiling_time),
            "breach_prob_q1": q1.breach_prob,
            "expected_return_q1": None,
            "range_midpoint_return_q1": round(midpoint_q1, 6),
            "expected_range_q1": q1.expected_range,
            "confidence_score": round(model_conf, 4),
            "model_confidence_score": round(model_conf, 4),
            "confidence_semantics": "mean_validation_interval_non_breach_rate",
            "directional_signal_available": False,
            "directional_expected_return_available": False,
            "ai_present": ai_present,
            "ai_effective_score": round(ai_eff, 6) if ai_present else 0.0,
            "ai_alignment_score": 0.0,
            "composite_signal_score": 0.0,
            "reward_risk_ratio": 0.0,
            "ai_weight": round(ai_weight, 4) if ai_present else 0.0,
            "expected_range_avg": round(expected_range_avg, 6),
            "m3_status": "ok",
            "m3_block_reason": None,
            "m3_timing_abstention_threshold": round(threshold, 6),
        }

        m3_reason = _m3_block_reason(row)
        if m3_reason is None:
            try:
                m3 = model.predict_m3(row)
            except Exception as exc:
                logger.exception(
                    "[forecasting] m3 prediction failed symbol=%s error=%s",
                    symbol,
                    exc,
                )
                m3 = None
                m3_reason = f"M3 prediction failed: {exc}"
        else:
            m3 = None

        if m3 is None:
            out.update(
                {
                    "floor_m3": None,
                    "floor_week_m3": None,
                    "floor_week_m3_confidence": None,
                    "floor_week_m3_top3": [],
                    "expected_return_m3": None,
                    "expected_range_m3": None,
                    "m3_status": "blocked",
                    "m3_block_reason": (
                        m3_reason
                        or "Champion m3 model unavailable for ticker features"
                    ),
                }
            )
        elif m3.floor_week_m3_confidence < threshold:
            # The value model can still be useful while timing has no credible
            # class separation. Never turn a near-uniform softmax into a fake
            # week merely because argmax must return something.
            out.update(
                {
                    "floor_m3": m3.floor_m3,
                    "floor_week_m3": None,
                    "floor_week_m3_confidence": m3.floor_week_m3_confidence,
                    "floor_week_m3_top3": [],
                    "expected_return_m3": None,
                    "expected_range_m3": m3.expected_range_m3,
                    "m3_status": "timing_abstained",
                    "m3_block_reason": (
                        "m3 timing abstained: max calibrated probability "
                        f"{m3.floor_week_m3_confidence:.4f} is below threshold "
                        f"{threshold:.4f}; floor value remains available"
                    ),
                }
            )
        else:
            out.update(
                {
                    "floor_m3": m3.floor_m3,
                    "floor_week_m3": m3.floor_week_m3,
                    "floor_week_m3_confidence": m3.floor_week_m3_confidence,
                    "floor_week_m3_top3": m3.floor_week_m3_top3,
                    "expected_return_m3": None,
                    "expected_range_m3": m3.expected_range_m3,
                }
            )

        m3_explanation = (
            "m3 timing abstained because calibrated week confidence was too low."
            if out["m3_status"] == "timing_abstained"
            else "m3 week 1..13 = semanas bursátiles relativas hacia adelante."
        )
        out["explanation_compact"] = (
            f"{symbol}: range-confidence={out['confidence_score']:.2f}, "
            f"d1_range={out['expected_range_d1']:.2f}, "
            "directional BUY/SELL disabled until a dedicated model proves "
            f"out-of-time lift. {m3_explanation}"
        )
        forecasts.append(render_horizon_time_labels(out, as_of=as_of))

    logger.info(
        "[forecasting] generation completed forecasts=%s blocked=%s",
        len(forecasts),
        len(blocked),
    )
    return {"forecasts": forecasts, "blocked": blocked}
