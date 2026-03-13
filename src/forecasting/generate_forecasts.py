from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from forecasting.load_models import load_champion_models
from forecasting.merge_ai_signal import merge_market_with_ai_signal
from forecasting.render_time_labels import render_horizon_time_labels

REQUIRED_MARKET_COLUMNS = ["symbol", "close", "high", "low"]
REQUIRED_M3_COLUMNS = ["close", "atr_14", "trend_context_m3", "drawdown_13w", "ai_horizon_alignment"]


def _blocked_reason(row: dict) -> str | None:
    missing = [c for c in REQUIRED_MARKET_COLUMNS if row.get(c) in (None, "")]
    if missing:
        return f"Missing market fields: {','.join(missing)}"
    return None


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _m3_block_reason(row: dict) -> str | None:
    missing = [c for c in REQUIRED_M3_COLUMNS if row.get(c) in (None, "")]
    if missing:
        return f"Missing m3 fields: {','.join(missing)}"
    return None


def generate_forecasts(market_rows: list[dict], ai_by_symbol: dict[str, dict], session: str, as_of: datetime | None = None) -> dict:
    as_of = as_of or datetime.now(tz=timezone.utc)
    model = load_champion_models()

    forecasts: list[dict] = []
    blocked: list[dict] = []

    if not model.is_available:
        for raw in market_rows:
            symbol = str(raw.get("symbol", "")).upper()
            blocked.append(
                {
                    "symbol": symbol,
                    "reason": "Pronóstico no disponible: faltan artefactos entrenados (value_champion.json y timing_champion.json)",
                }
            )
        return {"forecasts": forecasts, "blocked": blocked}

    for raw in market_rows:
        symbol = str(raw.get("symbol", "")).upper()
        reason = _blocked_reason(raw)
        if reason:
            blocked.append({"symbol": symbol, "reason": reason})
            continue

        row = merge_market_with_ai_signal(raw, ai_by_symbol.get(symbol), as_of=as_of)
        d1 = model.predict_d1(row)
        w1 = model.predict_w1(row)
        q1 = model.predict_q1(row)

        ai_eff = _safe_float(row.get("ai_effective_score"), 0.0)
        ai_weight = _safe_float(row.get("ai_weight"), 0.5)
        model_expected = (d1.expected_return + w1.expected_return + q1.expected_return) / 3
        expected_range_avg = (d1.expected_range + w1.expected_range + q1.expected_range) / 3

        confidence = max(0.05, min(0.99, 0.55 + 0.25 * ai_weight + 0.2 * max(0.0, 1 - d1.breach_prob)))
        alignment = max(-1.0, min(1.0, ai_eff + model_expected))
        composite = max(-1.0, min(1.0, 0.6 * model_expected + 0.4 * ai_eff))
        rr = (max(0.0, model_expected) + 1e-6) / max(0.01, d1.breach_prob)

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
            "expected_return_d1": d1.expected_return,
            "expected_range_d1": d1.expected_range,
            "floor_w1": w1.floor,
            "ceiling_w1": w1.ceiling,
            "floor_day_w1": int(w1.floor_time),
            "ceiling_day_w1": int(w1.ceiling_time),
            "breach_prob_w1": w1.breach_prob,
            "expected_return_w1": w1.expected_return,
            "expected_range_w1": w1.expected_range,
            "floor_q1": q1.floor,
            "ceiling_q1": q1.ceiling,
            "floor_day_q1": int(q1.floor_time),
            "ceiling_day_q1": int(q1.ceiling_time),
            "breach_prob_q1": q1.breach_prob,
            "expected_return_q1": q1.expected_return,
            "expected_range_q1": q1.expected_range,
            "confidence_score": round(confidence, 4),
            "ai_alignment_score": round(alignment, 6),
            "composite_signal_score": round(composite, 6),
            "reward_risk_ratio": round(rr, 6),
            "ai_weight": round(ai_weight, 4),
            "expected_range_avg": round(expected_range_avg, 6),
            "m3_status": "ok",
            "m3_block_reason": None,
        }

        m3_reason = _m3_block_reason(row)
        if m3_reason is None:
            m3 = model.predict_m3(row)
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
                    "m3_block_reason": m3_reason or "Champion m3 model unavailable for ticker features",
                }
            )
        else:
            out.update(
                {
                    "floor_m3": m3.floor_m3,
                    "floor_week_m3": m3.floor_week_m3,
                    "floor_week_m3_confidence": m3.floor_week_m3_confidence,
                    "floor_week_m3_top3": m3.floor_week_m3_top3,
                    "expected_return_m3": m3.expected_return_m3,
                    "expected_range_m3": m3.expected_range_m3,
                }
            )

        out["explanation_compact"] = (
            f"{symbol}: signal={out['composite_signal_score']:.3f}, conf={out['confidence_score']:.2f}, "
            f"ai_w={out['ai_weight']:.2f}, d1_range={out['expected_range_d1']:.2f}, rr={out['reward_risk_ratio']:.2f}. "
            f"m3 week 1..13 = semanas bursátiles relativas hacia adelante."
        )
        forecasts.append(render_horizon_time_labels(out, as_of=as_of))

    return {"forecasts": forecasts, "blocked": blocked}
