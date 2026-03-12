from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    family: str
    window: str
    leakage_safe: bool
    description: str


def get_feature_registry() -> list[FeatureSpec]:
    """Central registry for engineered features and targets."""

    return [
        FeatureSpec("ret_lag_1", "returns", "1 bar", True, "Return close(t)/close(t-1)-1."),
        FeatureSpec("ret_lag_2", "returns", "2 bars", True, "Return close(t)/close(t-2)-1."),
        FeatureSpec("ret_lag_5", "returns", "5 bars", True, "Return close(t)/close(t-5)-1."),
        FeatureSpec("ret_lag_10", "returns", "10 bars", True, "Return close(t)/close(t-10)-1."),
        FeatureSpec("downside_vol_20", "volatility", "20 bars", True, "Std of negative returns over 20 bars."),
        FeatureSpec("parkinson_vol_20", "volatility", "20 bars", True, "Range-based Parkinson volatility proxy."),
        FeatureSpec("momentum_10", "trend", "10 bars", True, "10-bar cumulative momentum."),
        FeatureSpec("momentum_20", "trend", "20 bars", True, "20-bar cumulative momentum."),
        FeatureSpec("vol_regime_score", "regime", "5/20 bars", True, "Volatility ratio rolling_vol_5/rolling_vol_20."),
        FeatureSpec("rsi_14", "momentum", "14 bars", True, "Relative Strength Index."),
        FeatureSpec("macd_12_26", "momentum", "12/26 EMA", True, "MACD line."),
        FeatureSpec("macd_signal_9", "momentum", "9 EMA", True, "Signal line for MACD."),
        FeatureSpec("macd_histogram", "momentum", "derived", True, "MACD histogram."),
        FeatureSpec("bollinger_width_20", "volatility", "20 bars", True, "Bollinger band width normalized by SMA20."),
        FeatureSpec("vwap_distance", "microstructure", "intraday", True, "Distance of close vs intraday VWAP."),
        FeatureSpec("rolling_vol_5", "volatility", "5 bars", True, "Std of lagged returns over 5 bars."),
        FeatureSpec("rolling_vol_20", "volatility", "20 bars", True, "Std of lagged returns over 20 bars."),
        FeatureSpec("atr_14", "volatility", "14 bars", True, "Average true range."),
        FeatureSpec("gap_open_to_prev_close", "microstructure", "1 day", True, "Open-to-prev-close gap."),
        FeatureSpec("relative_volume_20", "volume", "20 bars", True, "Volume / rolling mean volume."),
        FeatureSpec("dist_to_low_20", "price_location", "20 bars", True, "Distance from rolling low."),
        FeatureSpec("dist_to_high_20", "price_location", "20 bars", True, "Distance to rolling high."),
        FeatureSpec("sma_slope_5_20", "trend", "5/20 bars", True, "SMA(5) vs SMA(20) normalized spread."),
        FeatureSpec("beta_20", "relative_strength", "20 bars", True, "Rolling beta versus benchmark."),
        FeatureSpec("rel_strength_20", "relative_strength", "20 bars", True, "Cumulative return spread vs benchmark."),
        FeatureSpec("vol_regime", "regime", "5/20 bars", True, "Volatility regime low/normal/high."),
        FeatureSpec("recent_drawdown_20", "risk", "20 bars", True, "Close to rolling max drawdown."),
        FeatureSpec("intraday_range_5", "range", "5 bars", True, "Rolling intraday range intensity."),
        FeatureSpec("range_width_5", "range", "5 bars", True, "Rolling high-low width 5 bars."),
        FeatureSpec("range_width_20", "range", "20 bars", True, "Rolling high-low width 20 bars."),
        FeatureSpec("range_width_60", "range", "60 bars", True, "Rolling high-low width 60 bars."),
        FeatureSpec("price_position_in_range_20", "range", "20 bars", True, "Price location in rolling range."),
        FeatureSpec("trend_context_m3", "trend", "65 bars", True, "Distance vs SMA65 for intermediate trend context."),
        FeatureSpec("slope_4w", "trend", "20 bars", True, "Approx 4-week slope as cumulative return."),
        FeatureSpec("slope_8w", "trend", "40 bars", True, "Approx 8-week slope as cumulative return."),
        FeatureSpec("slope_13w", "trend", "65 bars", True, "Approx 13-week slope as cumulative return."),
        FeatureSpec("drawdown_13w", "risk", "65 bars", True, "Drawdown vs rolling 13-week peak."),
        FeatureSpec("range_compression_20_60", "range", "20/60 bars", True, "Compression/expansion ratio of short vs long range width."),
        FeatureSpec("rel_strength_4w", "relative_strength", "20 bars", True, "4-week relative strength vs benchmark."),
        FeatureSpec("rel_strength_8w", "relative_strength", "40 bars", True, "8-week relative strength vs benchmark."),
        FeatureSpec("rel_strength_13w", "relative_strength", "65 bars", True, "13-week relative strength vs benchmark."),
        FeatureSpec("month_of_year", "seasonality", "calendar", True, "Month index for simple seasonality."),
        FeatureSpec("relative_week_of_month", "seasonality", "calendar", True, "Relative week index within month (1..5)."),
        FeatureSpec("dist_to_low_3m", "price_location", "63 bars", True, "Distance to rolling 3-month low."),
        FeatureSpec("dist_to_low_6m", "price_location", "126 bars", True, "Distance to rolling 6-month low."),
        FeatureSpec("dist_to_low_12m", "price_location", "252 bars", True, "Distance to rolling 12-month low."),
        FeatureSpec("rolling_vol_60", "volatility", "60 bars", True, "Std of lagged returns over 60 bars."),
        FeatureSpec("vol_persistence_20_60", "volatility", "20/60 bars", True, "Volatility persistence ratio rolling_vol_20/rolling_vol_60."),
        FeatureSpec("range_amp_daily_5", "range", "5 days", True, "Average recent daily range amplitude over 5 days."),
        FeatureSpec("range_amp_daily_13", "range", "13 days", True, "Average recent daily range amplitude over 13 days."),
        FeatureSpec("ai_action", "ai", "point-in-time", True, "Action from AI sheet."),
        FeatureSpec("ai_conviction", "ai", "point-in-time", True, "Conviction from AI sheet."),
        FeatureSpec("ai_floor_d1", "ai", "point-in-time", True, "AI floor forecast d1."),
        FeatureSpec("ai_ceiling_d1", "ai", "point-in-time", True, "AI ceiling forecast d1."),
        FeatureSpec("ai_floor_w1", "ai", "point-in-time", True, "AI floor forecast w1."),
        FeatureSpec("ai_ceiling_w1", "ai", "point-in-time", True, "AI ceiling forecast w1."),
        FeatureSpec("ai_floor_q1", "ai", "point-in-time", True, "AI floor forecast q1."),
        FeatureSpec("ai_ceiling_q1", "ai", "point-in-time", True, "AI ceiling forecast q1."),
        FeatureSpec("ai_floor_m3", "ai", "point-in-time", True, "AI floor forecast m3."),
        FeatureSpec("ai_conviction_long", "ai", "point-in-time", True, "Long-horizon AI conviction."),
        FeatureSpec("ai_recency_long", "ai", "days", True, "Days since long-horizon AI update."),
        FeatureSpec("ai_horizon_alignment", "ai", "point-in-time", True, "Monotonic alignment of AI floor levels across horizons."),
        FeatureSpec("ai_recency", "ai", "days", True, "Days since AI sheet update."),
        FeatureSpec("ai_consensus_score", "ai", "point-in-time", True, "Aggregated consensus score from AI sheet."),
    ]


def build_missingness_report(rows: list[dict], columns: list[str]) -> list[dict]:
    total = max(1, len(rows))
    report: list[dict] = []
    for col in columns:
        missing = sum(1 for row in rows if row.get(col) is None)
        report.append(
            {
                "column": col,
                "missing_count": missing,
                "missing_ratio": missing / total,
                "coverage_ratio": 1 - (missing / total),
            }
        )
    return sorted(report, key=lambda x: x["missing_ratio"], reverse=True)
