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
