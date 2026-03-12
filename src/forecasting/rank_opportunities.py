from __future__ import annotations

from typing import Any, cast


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(cast(Any, value))
    except (TypeError, ValueError):
        return default


def _m3_context_for_top_pick(row: dict) -> tuple[str, list[str]]:
    warnings: list[str] = []
    note_parts: list[str] = []

    week = int(_safe_float(row.get("floor_week_m3"), 0.0))
    conf = _safe_float(row.get("floor_week_m3_confidence"), 0.0)
    m3_ret = _safe_float(row.get("expected_return_m3"), 0.0)
    d1_ret = _safe_float(row.get("expected_return_d1"), 0.0)
    w1_ret = _safe_float(row.get("expected_return_w1"), 0.0)
    q1_ret = _safe_float(row.get("expected_return_q1"), 0.0)

    if row.get("m3_status") == "blocked":
        warnings.append("m3_missing_for_ticker")
        note_parts.append("m3 no disponible: se mantiene operativa con d1/w1/q1 y se aplica prudencia por falta de contexto estructural")
    else:
        note_parts.append("m3 se usa como contexto estructural para riesgo y priorización; no como trigger intradía único")

    if week > 0 and week <= 2:
        warnings.append("m3_floor_week_near")
        note_parts.append(f"semana probable del mínimo trimestral cercana (w{week}, conf={conf:.2f}): endurecer riesgo")

    short_all_up = d1_ret > 0 and w1_ret > 0 and q1_ret > 0
    short_all_down = d1_ret < 0 and w1_ret < 0 and q1_ret < 0
    if m3_ret < 0 and short_all_up:
        warnings.append("m3_contradicts_short_horizons")
        note_parts.append("m3 contradice sesgo alcista de d1/w1/q1")
    if m3_ret > 0 and short_all_down:
        warnings.append("m3_contradicts_short_horizons")
        note_parts.append("m3 contradice sesgo bajista de d1/w1/q1")

    return "; ".join(note_parts), warnings


def _top_pick_payload(row: dict) -> dict:
    note, warnings = _m3_context_for_top_pick(row)
    return {
        **row,
        "floor_m3": _safe_float(row.get("floor_m3"), 0.0),
        "floor_week_m3": int(_safe_float(row.get("floor_week_m3"), 0.0)),
        "floor_week_m3_confidence": _safe_float(row.get("floor_week_m3_confidence"), 0.0),
        "floor_week_m3_start_date": row.get("floor_week_m3_start_date") or "",
        "floor_week_m3_end_date": row.get("floor_week_m3_end_date") or "",
        "m3_context_note": note,
        "m3_warnings": warnings,
    }


def rank_opportunities(forecasts: list[dict], blocked: list[dict], top_k: int = 10, low_conf_threshold: float = 0.45) -> dict:
    ordered = sorted(
        forecasts,
        key=lambda r: (
            _safe_float(r.get("composite_signal_score"), 0.0),
            _safe_float(r.get("reward_risk_ratio"), 0.0),
            _safe_float(r.get("confidence_score"), 0.0),
        ),
        reverse=True,
    )

    top = [_top_pick_payload(row) for row in ordered[:top_k]]
    low_conf = [r for r in forecasts if _safe_float(r.get("confidence_score"), 0.0) < low_conf_threshold]

    canonical = [
        {
            "symbol": r["symbol"],
            "composite_signal_score": r["composite_signal_score"],
            "confidence_score": r["confidence_score"],
            "reward_risk_ratio": r["reward_risk_ratio"],
            "breach_prob_d1": r["breach_prob_d1"],
            "expected_return_d1": r["expected_return_d1"],
            "floor_d1": r["floor_d1"],
            "ceiling_d1": r["ceiling_d1"],
            "floor_time_bucket_d1": r["floor_time_bucket_d1"],
            "ceiling_time_bucket_d1": r["ceiling_time_bucket_d1"],
            "floor_m3": r.get("floor_m3"),
            "floor_week_m3": r.get("floor_week_m3"),
            "floor_week_m3_confidence": r.get("floor_week_m3_confidence"),
            "floor_week_m3_top3": r.get("floor_week_m3_top3", []),
            "floor_week_m3_start_date": r.get("floor_week_m3_start_date"),
            "floor_week_m3_end_date": r.get("floor_week_m3_end_date"),
            "expected_return_m3": r.get("expected_return_m3"),
            "expected_range_m3": r.get("expected_range_m3"),
            "m3_status": r.get("m3_status"),
            "m3_block_reason": r.get("m3_block_reason"),
        }
        for r in forecasts
    ]

    dashboard = [
        {
            "ticker": r["symbol"],
            "score": r["composite_signal_score"],
            "confidence": r["confidence_score"],
            "message": r["explanation_compact"],
            "w1_floor_date": r.get("floor_date_w1"),
            "w1_ceiling_date": r.get("ceiling_date_w1"),
            "q1_floor_date": r.get("floor_date_q1"),
            "q1_ceiling_date": r.get("ceiling_date_q1"),
            "m3_floor": r.get("floor_m3"),
            "m3_week_index": r.get("floor_week_m3"),
            "m3_week_confidence": r.get("floor_week_m3_confidence"),
            "m3_week_top3": r.get("floor_week_m3_top3", []),
            "m3_week_start_date": r.get("floor_week_m3_start_date"),
            "m3_week_end_date": r.get("floor_week_m3_end_date"),
            "m3_week_label_human": r.get("floor_week_m3_label_human"),
            "m3_status": r.get("m3_status"),
        }
        for r in forecasts
    ]

    return {
        "top_opportunities": top,
        "low_confidence_list": low_conf,
        "blocked_list": blocked,
        "canonical_strategy_output": canonical,
        "human_friendly_dashboard": dashboard,
    }
