from __future__ import annotations

from execution.risk_gateway import RiskPolicy, approve_signal_batch


def _policy() -> RiskPolicy:
    return RiskPolicy(
        nav_usd=1_000_000.0,
        max_position_notional_usd=50_000.0,
        max_gross_exposure_usd=500_000.0,
        max_single_name_weight=0.08,
        max_sector_weight=0.25,
        daily_loss_limit_bps=150.0,
        kill_switch_enabled=True,
    )


def test_cross_horizon_conflict_prefers_existing_position_exit() -> None:
    signals = [
        {"symbol": "AAPL", "action": "BUY", "horizon": "d1", "confidence": 0.99, "rationale": "add"},
        {"symbol": "AAPL", "action": "SELL", "horizon": "w1", "confidence": 0.60, "rationale": "exit"},
    ]
    market = [{"symbol": "AAPL", "sector": "Technology", "open": 100, "high": 100, "low": 100, "close": 100}]

    result = approve_signal_batch(
        signals,
        market,
        policy=_policy(),
        existing_gross_notional_usd=50_000.0,
        existing_symbol_notional_usd={"AAPL": 50_000.0},
        existing_sector_notional_usd={"Technology": 50_000.0},
        existing_symbol_quantity={"AAPL": 500},
    )

    assert len(result.orders) == 1
    assert result.orders[0]["side"] == "SELL"
    assert result.orders[0]["quantity"] == 500
    assert result.orders[0]["metadata"]["risk_action"] == "decrease_exposure"
    assert any(item["reason"] == "cross_horizon_conflict_prefer_decrease" for item in result.rejected)


def test_risk_sizing_uses_conservative_ohlc_price_not_close_only() -> None:
    signal = {"symbol": "AAPL", "action": "BUY", "horizon": "d1", "confidence": 0.9, "rationale": "entry"}
    market = [
        {
            "symbol": "AAPL",
            "sector": "Technology",
            "open": 100.0,
            "high": 110.0,
            "low": 95.0,
            "close": 100.0,
        }
    ]

    result = approve_signal_batch([signal], market, policy=_policy())

    assert len(result.orders) == 1
    order = result.orders[0]
    assert order["quantity"] == 454
    assert order["metadata"]["risk_reference_price"] == 110.0
    assert order["metadata"]["approved_notional_usd"] <= 50_000.0
