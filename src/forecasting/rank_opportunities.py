from __future__ import annotations

from typing import Any, cast


RANKING_BASIS = "forecast_quality_only_no_directional_alpha"


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

    if row.get("m3_status") == "blocked":
        warnings.append("m3_missing_for_ticker")
        note_parts.append(
            "m3 no disponible: d1/w1/q1 se muestran como rangos, no como señal direccional"
        )
    else:
        note_parts.append(
            "m3 se usa como contexto de riesgo/timing; no como trigger direccional"
        )

    if 0 < week <= 2:
        warnings.append("m3_floor_week_near")
        note_parts.append(
            f"semana probable del mínimo m3 cercana (w{week}, conf={conf:.2f})"
        )

    if not bool(row.get("directional_signal_available", False)):
        warnings.append("directional_alpha_not_available")
        note_parts.append(
            "BUY/SELL deshabilitado hasta que un modelo direccional pruebe lift out-of-time"
        )

    return "; ".join(note_parts), warnings


def _top_pick_payload(row: dict) -> dict:
    note, warnings = _m3_context_for_top_pick(row)
    return {
        **row,
        "ranking_basis": RANKING_BASIS,
        "ranking_score": _safe_float(row.get("confidence_score"), 0.0),
        "floor_m3": _safe_float(row.get("floor_m3"), 0.0),
        "floor_week_m3": int(_safe_float(row.get("floor_week_m3"), 0.0)),
        "floor_week_m3_confidence": _safe_float(
            row.get("floor_week_m3_confidence"), 0.0
        ),
        "floor_week_m3_start_date": row.get("floor_week_m3_start_date") or "",
        "floor_week_m3_end_date": row.get("floor_week_m3_end_date") or "",
        "m3_context_note": note,
        "m3_warnings": warnings,
    }


def rank_opportunities(
    forecasts: list[dict],
    blocked: list[dict],
    top_k: int = 10,
    low_conf_threshold: float = 0.45,
) -> dict:
    """Rank forecast *quality* only until directional alpha is validated.

    The legacy name ``top_opportunities`` is retained for API/frontend
    compatibility, but every row carries ``ranking_basis`` so it cannot be
    interpreted as a BUY recommendation.
    """

    ordered = sorted(
        forecasts,
        key=lambda row: (
            _safe_float(row.get("confidence_score"), 0.0),
            -_safe_float(row.get("breach_prob_w1"), 1.0),
            -_safe_float(row.get("breach_prob_q1"), 1.0),
        ),
        reverse=True,
    )

    top = [_top_pick_payload(row) for row in ordered[:top_k]]
    low_conf = [
        row
        for row in forecasts
        if _safe_float(row.get("confidence_score"), 0.0) < low_conf_threshold
    ]

    canonical = [
        {
            "symbol": row["symbol"],
            "ranking_basis": RANKING_BASIS,
            "directional_signal_available": bool(
                row.get("directional_signal_available", False)
            ),
            "composite_signal_score": 0.0,
            "confidence_score": row["confidence_score"],
            "reward_risk_ratio": 0.0,
            "breach_prob_d1": row["breach_prob_d1"],
            "expected_return_d1": None,
            "range_midpoint_return_d1": row.get("range_midpoint_return_d1"),
            "floor_d1": row["floor_d1"],
            "ceiling_d1": row["ceiling_d1"],
            "floor_time_bucket_d1": row["floor_time_bucket_d1"],
            "ceiling_time_bucket_d1": row["ceiling_time_bucket_d1"],
            "floor_m3": row.get("floor_m3"),
            "floor_week_m3": row.get("floor_week_m3"),
            "floor_week_m3_confidence": row.get("floor_week_m3_confidence"),
            "floor_week_m3_top3": row.get("floor_week_m3_top3", []),
            "floor_week_m3_start_date": row.get("floor_week_m3_start_date"),
            "floor_week_m3_end_date": row.get("floor_week_m3_end_date"),
            "expected_return_m3": None,
            "expected_range_m3": row.get("expected_range_m3"),
            "m3_status": row.get("m3_status"),
            "m3_block_reason": row.get("m3_block_reason"),
        }
        for row in forecasts
    ]

    dashboard = [
        {
            "ticker": row["symbol"],
            "score": row["confidence_score"],
            "score_semantics": RANKING_BASIS,
            "directional_signal_available": bool(
                row.get("directional_signal_available", False)
            ),
            "confidence": row["confidence_score"],
            "message": row["explanation_compact"],
            "w1_floor_date": row.get("floor_date_w1"),
            "w1_ceiling_date": row.get("ceiling_date_w1"),
            "q1_floor_date": row.get("floor_date_q1"),
            "q1_ceiling_date": row.get("ceiling_date_q1"),
            "m3_floor": row.get("floor_m3"),
            "m3_week_index": row.get("floor_week_m3"),
            "m3_week_confidence": row.get("floor_week_m3_confidence"),
            "m3_week_top3": row.get("floor_week_m3_top3", []),
            "m3_week_start_date": row.get("floor_week_m3_start_date"),
            "m3_week_end_date": row.get("floor_week_m3_end_date"),
            "m3_week_label_human": row.get("floor_week_m3_label_human"),
            "m3_status": row.get("m3_status"),
        }
        for row in forecasts
    ]

    return {
        "ranking_basis": RANKING_BASIS,
        "top_opportunities": top,
        "low_confidence_list": low_conf,
        "blocked_list": blocked,
        "canonical_strategy_output": canonical,
        "human_friendly_dashboard": dashboard,
    }
