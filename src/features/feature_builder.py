from __future__ import annotations

from datetime import datetime
from statistics import mean, pstdev


def _to_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


def _safe_mean(values: list[float]) -> float | None:
    if not values:
        return None
    return mean(values)


def _to_float_or_fallback(value: object, fallback: float) -> float:
    if value is None:
        return fallback
    if isinstance(value, (int, float, str, bytes)):
        return float(value)
    return fallback


def _rolling(values: list[float], end_idx: int, window: int) -> list[float]:
    start = max(0, end_idx - window + 1)
    return values[start : end_idx + 1]


def _ema(values: list[float], span: int) -> float | None:
    if not values:
        return None
    alpha = 2 / (span + 1)
    out = values[0]
    for v in values[1:]:
        out = alpha * v + (1 - alpha) * out
    return out


def _rsi(closes: list[float], idx: int, window: int = 14) -> float | None:
    if idx < 1:
        return None
    changes = [closes[i] - closes[i - 1] for i in range(max(1, idx - window + 1), idx + 1)]
    if not changes:
        return None
    gains = [c for c in changes if c > 0]
    losses = [-c for c in changes if c < 0]
    avg_gain = sum(gains) / window if gains else 0.0
    avg_loss = sum(losses) / window if losses else 0.0
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def build_features(rows: list[dict]) -> list[dict]:
    """Build leakage-safe features using only data up to each row timestamp."""

    rows = sorted(rows, key=lambda r: (r["symbol"], _to_datetime(r["timestamp"])))
    by_symbol: dict[str, list[dict]] = {}
    for row in rows:
        by_symbol.setdefault(row["symbol"], []).append(dict(row))

    output: list[dict] = []
    for symbol_rows in by_symbol.values():
        closes: list[float] = []
        highs: list[float] = []
        lows: list[float] = []
        volumes: list[float] = []
        bench_closes: list[float] = []
        rets: list[float] = []
        bench_rets: list[float] = []
        tr_values: list[float] = []
        day_closes: list[float] = []
        day_bench_closes: list[float] = []
        day_ranges: list[float] = []
        prev_day_last_close: float | None = None
        current_day = None
        day_vwap_num = 0.0
        day_vwap_den = 0.0

        for idx, row in enumerate(symbol_rows):
            ts = _to_datetime(row["timestamp"])
            day = ts.date()
            close = float(row["close"])
            high = float(row["high"])
            low = float(row["low"])
            open_ = float(row["open"])
            volume = float(row.get("volume", 0.0))
            bench_close = _to_float_or_fallback(row.get("benchmark_close"), close)
            typical_price = (high + low + close) / 3

            if idx == 0:
                ret_1 = None
                bench_ret_1 = None
            else:
                ret_1 = close / closes[-1] - 1.0
                bench_ret_1 = bench_close / bench_closes[-1] - 1.0

            rets.append(ret_1 if ret_1 is not None else 0.0)
            bench_rets.append(bench_ret_1 if bench_ret_1 is not None else 0.0)

            prev_close = closes[-1] if closes else close
            true_range = max(high - low, abs(high - prev_close), abs(low - prev_close))
            tr_values.append(true_range)

            closes.append(close)
            highs.append(high)
            lows.append(low)
            volumes.append(volume)
            bench_closes.append(bench_close)

            row["ret_lag_1"] = ret_1
            row["ret_lag_2"] = None if idx < 2 else close / closes[-3] - 1.0
            row["ret_lag_5"] = None if idx < 5 else close / closes[-6] - 1.0
            row["ret_lag_10"] = None if idx < 10 else close / closes[-11] - 1.0

            win_5_rets = _rolling(rets, idx, 5)
            win_20_rets = _rolling(rets, idx, 20)
            row["rolling_vol_5"] = None if len(win_5_rets) < 2 else pstdev(win_5_rets)
            row["rolling_vol_20"] = None if len(win_20_rets) < 2 else pstdev(win_20_rets)
            win_60_rets = _rolling(rets, idx, 60)
            row["rolling_vol_60"] = None if len(win_60_rets) < 2 else pstdev(win_60_rets)
            downside_20 = [r for r in win_20_rets if r < 0]
            row["downside_vol_20"] = None if len(downside_20) < 2 else pstdev(downside_20)

            atr_win = _rolling(tr_values, idx, 14)
            row["atr_14"] = _safe_mean(atr_win)
            hl_ratios = []
            for high_price, low_price in zip(_rolling(highs, idx, 20), _rolling(lows, idx, 20)):
                if low_price > 0:
                    hl_ratios.append((high_price / low_price) ** 2)
            parkinson_mean = _safe_mean(hl_ratios)
            row["parkinson_vol_20"] = None if parkinson_mean is None or len(hl_ratios) < 2 else parkinson_mean ** 0.5

            if current_day != day:
                row["gap_open_to_prev_close"] = None if prev_day_last_close is None else open_ / prev_day_last_close - 1.0
                current_day = day
                day_vwap_num = 0.0
                day_vwap_den = 0.0
            else:
                row["gap_open_to_prev_close"] = 0.0

            if idx + 1 < len(symbol_rows):
                next_day = _to_datetime(symbol_rows[idx + 1]["timestamp"]).date()
                if next_day != day:
                    prev_day_last_close = close
                    day_closes.append(close)
                    day_bench_closes.append(bench_close)
                    day_high = max(_rolling(highs, idx, 5))
                    day_low = min(_rolling(lows, idx, 5))
                    day_ranges.append((day_high - day_low) / close if close else 0.0)
            elif idx == len(symbol_rows) - 1:
                day_closes.append(close)
                day_bench_closes.append(bench_close)
                day_high = max(_rolling(highs, idx, 5))
                day_low = min(_rolling(lows, idx, 5))
                day_ranges.append((day_high - day_low) / close if close else 0.0)

            vol20 = _safe_mean(_rolling(volumes, idx, 20))
            if vol20 is None or vol20 == 0:
                row["relative_volume_20"] = None
            else:
                row["relative_volume_20"] = volume / vol20

            low20 = min(_rolling(lows, idx, 20))
            high20 = max(_rolling(highs, idx, 20))
            row["dist_to_low_20"] = None if low20 == 0 else close / low20 - 1.0
            row["dist_to_high_20"] = None if close == 0 else high20 / close - 1.0

            sma5 = _safe_mean(_rolling(closes, idx, 5))
            sma20 = _safe_mean(_rolling(closes, idx, 20))
            row["sma_slope_5_20"] = None if not sma5 or not sma20 else (sma5 - sma20) / sma20

            a_rets = _rolling(rets, idx, 20)
            b_rets = _rolling(bench_rets, idx, 20)
            if len(a_rets) < 2 or len(b_rets) < 2:
                row["beta_20"] = None
            else:
                mean_a = mean(a_rets)
                mean_b = mean(b_rets)
                cov = mean([(a - mean_a) * (b - mean_b) for a, b in zip(a_rets, b_rets)])
                var_b = mean([(b - mean_b) ** 2 for b in b_rets])
                row["beta_20"] = None if var_b == 0 else cov / var_b

            if idx < 20:
                row["rel_strength_20"] = None
                row["momentum_20"] = None
            else:
                asset_cum = closes[-1] / closes[-21] - 1.0
                bench_cum = bench_closes[-1] / bench_closes[-21] - 1.0
                row["rel_strength_20"] = asset_cum - bench_cum
                row["momentum_20"] = asset_cum
            row["momentum_10"] = None if idx < 10 else closes[-1] / closes[-11] - 1.0

            vol5 = row["rolling_vol_5"]
            vol20_ = row["rolling_vol_20"]
            if vol5 is None or vol20_ in (None, 0):
                row["vol_regime"] = None
            else:
                ratio = vol5 / vol20_
                row["vol_regime_score"] = ratio
                if ratio < 0.8:
                    row["vol_regime"] = "LOW"
                elif ratio > 1.2:
                    row["vol_regime"] = "HIGH"
                else:
                    row["vol_regime"] = "NORMAL"

            max_close20 = max(_rolling(closes, idx, 20))
            row["recent_drawdown_20"] = None if max_close20 == 0 else close / max_close20 - 1.0

            valid_ranges = [
                (high_price - low_price) / close_price
                for high_price, low_price, close_price in zip(highs, lows, closes)
                if close_price != 0
            ]
            row["intraday_range_5"] = _safe_mean(_rolling(valid_ranges, len(valid_ranges) - 1, 5)) if valid_ranges else None
            row["range_width_5"] = None if close == 0 else (max(_rolling(highs, idx, 5)) - min(_rolling(lows, idx, 5))) / close
            row["range_width_20"] = None if close == 0 else (max(_rolling(highs, idx, 20)) - min(_rolling(lows, idx, 20))) / close
            row["range_width_60"] = None if close == 0 else (max(_rolling(highs, idx, 60)) - min(_rolling(lows, idx, 60))) / close
            denom = high20 - low20
            row["price_position_in_range_20"] = None if denom == 0 else (close - low20) / denom

            row["trend_context_m3"] = None
            row["slope_4w"] = None if idx < 20 or closes[-21] == 0 else close / closes[-21] - 1.0
            row["slope_8w"] = None if idx < 40 or closes[-41] == 0 else close / closes[-41] - 1.0
            row["slope_13w"] = None if idx < 65 or closes[-66] == 0 else close / closes[-66] - 1.0
            sma65 = _safe_mean(_rolling(closes, idx, 65))
            if sma65 is not None and sma65 != 0:
                sma65_value = float(sma65)
                row["trend_context_m3"] = close / sma65_value - 1.0

            max_close65 = max(_rolling(closes, idx, 65))
            row["drawdown_13w"] = None if max_close65 == 0 else close / max_close65 - 1.0
            if row.get("range_width_20") in (None, 0) or row.get("range_width_60") is None:
                row["range_compression_20_60"] = None
            else:
                row["range_compression_20_60"] = row["range_width_20"] / row["range_width_60"] if row["range_width_60"] != 0 else None

            row["rel_strength_4w"] = None if idx < 20 else row.get("rel_strength_20")
            row["rel_strength_8w"] = None if idx < 40 else (close / closes[-41] - 1.0) - (bench_close / bench_closes[-41] - 1.0)
            row["rel_strength_13w"] = None if idx < 65 else (close / closes[-66] - 1.0) - (bench_close / bench_closes[-66] - 1.0)

            row["month_of_year"] = ts.month
            row["relative_week_of_month"] = ((ts.day - 1) // 7) + 1

            low63 = min(_rolling(lows, idx, 63))
            low126 = min(_rolling(lows, idx, 126))
            low252 = min(_rolling(lows, idx, 252))
            row["dist_to_low_3m"] = None if low63 == 0 else close / low63 - 1.0
            row["dist_to_low_6m"] = None if low126 == 0 else close / low126 - 1.0
            row["dist_to_low_12m"] = None if low252 == 0 else close / low252 - 1.0

            vol60 = row.get("rolling_vol_60")
            vol20_now = row.get("rolling_vol_20")
            row["vol_persistence_20_60"] = None if vol20_now in (None, 0) or vol60 is None else vol20_now / vol60

            row["range_amp_daily_5"] = _safe_mean(_rolling(day_ranges, len(day_ranges) - 1, 5)) if day_ranges else None
            row["range_amp_daily_13"] = _safe_mean(_rolling(day_ranges, len(day_ranges) - 1, 13)) if day_ranges else None

            row["rsi_14"] = _rsi(closes, idx, 14)
            ema12 = _ema(_rolling(closes, idx, 40), 12)
            ema26 = _ema(_rolling(closes, idx, 40), 26)
            macd = None if ema12 is None or ema26 is None else ema12 - ema26
            row["macd_12_26"] = macd
            recent_macd = [
                (_ema(_rolling(closes, i, 40), 12) or 0.0) - (_ema(_rolling(closes, i, 40), 26) or 0.0)
                for i in range(max(0, idx - 8), idx + 1)
            ]
            signal = _ema(recent_macd, 9)
            row["macd_signal_9"] = signal
            row["macd_histogram"] = None if macd is None or signal is None else macd - signal

            mid20 = _safe_mean(_rolling(closes, idx, 20))
            std20 = None if len(_rolling(closes, idx, 20)) < 2 else pstdev(_rolling(closes, idx, 20))
            if mid20 is None or mid20 == 0 or std20 is None:
                row["bollinger_width_20"] = None
            else:
                row["bollinger_width_20"] = (4 * std20) / mid20

            day_vwap_num += typical_price * volume
            day_vwap_den += volume
            day_vwap = None if day_vwap_den == 0 else day_vwap_num / day_vwap_den
            if day_vwap is None or day_vwap == 0:
                row["vwap_distance"] = None
            else:
                row["vwap_distance"] = close / day_vwap - 1.0

            # AI-derived features are point-in-time columns. Preserve supplied values and derive recency if possible.
            row["ai_action"] = row.get("ai_action")
            row["ai_conviction"] = row.get("ai_conviction")
            row["ai_floor_d1"] = row.get("ai_floor_d1")
            row["ai_ceiling_d1"] = row.get("ai_ceiling_d1")
            row["ai_floor_w1"] = row.get("ai_floor_w1")
            row["ai_ceiling_w1"] = row.get("ai_ceiling_w1")
            row["ai_floor_q1"] = row.get("ai_floor_q1")
            row["ai_ceiling_q1"] = row.get("ai_ceiling_q1")
            row["ai_floor_m3"] = row.get("ai_floor_m3")
            row["ai_conviction_long"] = row.get("ai_conviction_long", row.get("ai_conviction"))
            row["ai_recency_long"] = row.get("ai_recency_long")
            ai_updated = row.get("ai_updated_at")
            if ai_updated is None:
                row["ai_recency"] = row.get("ai_recency")
            else:
                row["ai_recency"] = max(0, (ts - _to_datetime(ai_updated)).days)
            row["ai_consensus_score"] = row.get("ai_consensus_score")

            floor_d1 = row.get("ai_floor_d1")
            floor_w1 = row.get("ai_floor_w1")
            floor_q1 = row.get("ai_floor_q1")
            floor_m3 = row.get("ai_floor_m3")
            if (
                isinstance(floor_d1, (int, float))
                and isinstance(floor_w1, (int, float))
                and isinstance(floor_q1, (int, float))
                and isinstance(floor_m3, (int, float))
            ):
                floor_d1_value = float(floor_d1)
                floor_w1_value = float(floor_w1)
                floor_q1_value = float(floor_q1)
                floor_m3_value = float(floor_m3)
                row["ai_horizon_alignment"] = float(
                    floor_d1_value >= floor_w1_value >= floor_q1_value >= floor_m3_value
                )
            else:
                row["ai_horizon_alignment"] = None

            if row.get("ai_recency_long") is None and row.get("ai_recency") is not None:
                row["ai_recency_long"] = row["ai_recency"]

            output.append(row)

    return sorted(output, key=lambda r: (r["symbol"], _to_datetime(r["timestamp"])))
