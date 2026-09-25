from __future__ import annotations

from league.market_features import _feature_row


def _bars(symbol: str, *, multiplier: float) -> list[dict]:
    rows = []
    close = 100.0
    for index in range(70):
        step = (0.004 + (index % 5) * 0.001) * multiplier
        close *= 1.0 + step
        rows.append(
            {
                "symbol": symbol,
                "timestamp": f"2026-01-{index + 1:02d}T21:00:00+00:00",
                "open": close * 0.998,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": 1_000_000.0 + index * 10_000.0,
            }
        )
    return rows


def test_league_feature_row_exposes_regime_and_cross_sectional_parity() -> None:
    asset = _bars("AAA", multiplier=1.25)
    benchmark = _bars("SPY", multiplier=1.0)

    row = _feature_row("AAA", asset, benchmark)

    assert row is not None
    assert row["rolling_vol_5"] is not None
    assert row["rolling_vol_20"] is not None
    assert row["vol_regime_score"] is not None
    assert row["vol_regime"] in {"LOW", "NORMAL", "HIGH"}
    assert row["rel_strength_4w"] is not None
    assert row["rel_strength_8w"] is not None
    assert row["rel_strength_13w"] is not None
    assert row["beta_20"] is not None


def test_relative_strength_horizons_preserve_benchmark_comparison() -> None:
    asset = _bars("AAA", multiplier=1.40)
    benchmark = _bars("SPY", multiplier=0.80)

    row = _feature_row("AAA", asset, benchmark)

    assert row is not None
    assert row["rel_strength_4w"] > 0
    assert row["rel_strength_8w"] > 0
    assert row["rel_strength_13w"] > 0
