from __future__ import annotations


def rank_opportunities(forecasts: list[dict], blocked: list[dict], top_k: int = 10, low_conf_threshold: float = 0.45) -> dict:
    ordered = sorted(
        forecasts,
        key=lambda r: (
            float(r.get("composite_signal_score", 0.0)),
            float(r.get("reward_risk_ratio", 0.0)),
            float(r.get("confidence_score", 0.0)),
        ),
        reverse=True,
    )

    top = ordered[:top_k]
    low_conf = [r for r in forecasts if float(r.get("confidence_score", 0.0)) < low_conf_threshold]

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
