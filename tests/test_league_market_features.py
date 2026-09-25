from __future__ import annotations

from datetime import date, timedelta

from league.market_features import _feature_row


def _bars(symbol: str, *, asset: bool) -> list[dict]:
    rows: list[dict] = []
    close = 100.0
    start = date(2026, 5, 1)
    for index in range(70):
        if asset:
            daily_return = 0.0010 + (index % 5) * 0.00035
        else:
            daily_return = 0.0005 + (index % 3) * 0.00020
        close *= 1.0 + daily_return
        rows.append(
            {
                "symbol": symbol,
                "timestamp": (start + timedelta(days=index)).isoformat(),
                "open": close * 0.998,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": 1_000_000 + index * 1_000,
            }
        )
    return rows


def test_league_feature_row_exposes_regime_relative_strength_and_beta() -> None:
    asset = _bars("AAA", asset=True)
    spy = _bars("SPY", asset=False)

    row = _feature_row("AAA", asset, spy)

    assert row is not None
    assert row["rel_strength_4w"] is not None
    assert row["rel_strength_8w"] is not None
    assert row["rel_strength_13w"] is not None
    assert row["rel_strength_4w"] > 0
    assert row["rel_strength_13w"] > 0
    assert row["beta_20"] is not None
    assert row["rolling_vol_5"] is not None
    assert row["rolling_vol_20"] is not None
    assert row["vol_regime_score"] is not None
    assert row["vol_regime"] in {"LOW", "NORMAL", "HIGH"}
