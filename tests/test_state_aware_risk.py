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
        intraday_drawdown_limit_bps=250.0,
        position_loss_add_block_bps=150.0,
        position_profit_add_block_bps=300.0,
        min_drawdown_risk_scale=0.50,
    )


def _market() -> list[dict]:
    return [
        {
            "symbol": "AAPL",
            "sector": "Technology",
            "open": 100.0,
            "high": 100.0,
            "low": 100.0,
            "close": 100.0,
        }
    ]


def _buy() -> list[dict]:
    return [
        {
            "symbol": "AAPL",
            "action": "BUY",
            "horizon": "d1",
            "confidence": 0.90,
            "rationale": "state-aware entry",
        }
    ]


def test_intraday_drawdown_scales_new_risk_before_hard_limit() -> None:
    result = approve_signal_batch(
        _buy(),
        _market(),
        policy=_policy(),
        current_equity_usd=990_000.0,
        intraday_peak_equity_usd=1_000_000.0,
    )

    assert len(result.orders) == 1
    order = result.orders[0]
    assert order["quantity"] == 400
    assert order["metadata"]["state_context"]["intraday_drawdown_bps"] == 100.0
    assert order["metadata"]["state_context"]["risk_scale"] == 0.8


def test_intraday_drawdown_kill_switch_blocks_only_exposure_increase() -> None:
    blocked = approve_signal_batch(
        _buy(),
        _market(),
        policy=_policy(),
        current_equity_usd=970_000.0,
        intraday_peak_equity_usd=1_000_000.0,
    )

    assert blocked.orders == []
    assert blocked.rejected[0]["reason"] == "kill_switch: intraday_drawdown_limit"

    sell = [
        {
            "symbol": "AAPL",
            "action": "SELL",
            "horizon": "d1",
            "confidence": 0.90,
            "rationale": "defensive exit",
        }
    ]
    reducing = approve_signal_batch(
        sell,
        _market(),
        policy=_policy(),
        current_equity_usd=970_000.0,
        intraday_peak_equity_usd=1_000_000.0,
        existing_gross_notional_usd=50_000.0,
        existing_symbol_notional_usd={"AAPL": 50_000.0},
        existing_sector_notional_usd={"Technology": 50_000.0},
        existing_symbol_quantity={"AAPL": 500},
        existing_symbol_return_bps={"AAPL": -300.0},
    )

    assert len(reducing.orders) == 1
    assert reducing.orders[0]["side"] == "SELL"
    assert reducing.orders[0]["metadata"]["risk_action"] == "decrease_exposure"


def test_state_aware_overlay_does_not_average_down() -> None:
    result = approve_signal_batch(
        _buy(),
        _market(),
        policy=_policy(),
        existing_gross_notional_usd=10_000.0,
        existing_symbol_notional_usd={"AAPL": 10_000.0},
        existing_sector_notional_usd={"Technology": 10_000.0},
        existing_symbol_quantity={"AAPL": 100},
        existing_symbol_return_bps={"AAPL": -200.0},
    )

    assert result.orders == []
    assert result.rejected[0]["reason"] == "state_aware: do_not_average_down"


def test_state_aware_overlay_does_not_chase_extended_position() -> None:
    result = approve_signal_batch(
        _buy(),
        _market(),
        policy=_policy(),
        existing_gross_notional_usd=10_000.0,
        existing_symbol_notional_usd={"AAPL": 10_000.0},
        existing_sector_notional_usd={"Technology": 10_000.0},
        existing_symbol_quantity={"AAPL": 100},
        existing_symbol_return_bps={"AAPL": 350.0},
    )

    assert result.orders == []
    assert (
        result.rejected[0]["reason"]
        == "state_aware: do_not_chase_extended_position"
    )


def test_state_context_audits_realized_and_unrealized_pnl() -> None:
    result = approve_signal_batch(
        _buy(),
        _market(),
        policy=_policy(),
        realized_pnl_usd=1_000.0,
        unrealized_pnl_usd=-250.0,
        current_equity_usd=1_000_750.0,
        intraday_peak_equity_usd=1_002_000.0,
    )

    context = result.orders[0]["metadata"]["state_context"]
    assert context["realized_pnl_usd"] == 1_000.0
    assert context["unrealized_pnl_usd"] == -250.0
    assert context["marked_pnl_usd"] == 750.0
