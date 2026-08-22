from __future__ import annotations

import pytest

from backtest.cost_model import CostModelConfig
from execution.gateway import PaperExecutionGateway
from execution.risk_gateway import RiskPolicy, approve_signal_batch
from forecasting.parity_models import ParityChampionModelSet
from models.classic_horizon_predictor import predict_family_delta
from models.train_classic_horizons import (
    _PreparedRow,
    _predict_boosted_stumps,
    _predict_evt,
    _predict_linear,
)


def _prepared(features: dict[str, float]) -> _PreparedRow:
    return _PreparedRow(
        row={},
        close=100.0,
        floor_delta=0.02,
        ceiling_delta=0.03,
        features=features,
    )


def test_evt_train_and_shared_serving_predictor_match() -> None:
    features = {"atr_14": 0.02, "trend_context_m3": 1.0}
    params = {
        "global": 0.03,
        "table": {"v1:up": 0.011, "v2:up": 0.022, "v3:up": 0.033},
        "vol_cuts": [0.01, 0.025],
        "bins": 3,
    }
    train_value = _predict_evt(_prepared(features), params)
    serve_value = predict_family_delta("evt_changepoint_hybrid", params, features)
    assert serve_value == train_value == 0.022


def test_boosted_stumps_train_and_shared_serving_predictor_match() -> None:
    features = {"atr_14": 0.02, "rel_strength_20": 0.1}
    params = {
        "base": 0.01,
        "lr": 0.5,
        "stumps": [
            {"feature": "atr_14", "threshold": 0.015, "left": -0.002, "right": 0.004},
            {"feature": "rel_strength_20", "threshold": 0.0, "left": -0.003, "right": 0.002},
        ],
    }
    train_value = _predict_boosted_stumps(_prepared(features), params)
    serve_value = predict_family_delta("xgboost", params, features)
    assert serve_value == train_value


def test_linear_train_and_shared_serving_predictor_match() -> None:
    features = {"atr_14": 0.02, "trend_context_m3": -0.1}
    names = ("atr_14", "trend_context_m3")
    weights = {"atr_14": 0.4, "trend_context_m3": -0.03}
    bias = 0.012
    params = {"weights": weights, "bias": bias, "features": list(names)}
    train_value = _predict_linear(_prepared(features), weights, bias, names)
    assert predict_family_delta("quantile_elastic_net", params, features) == train_value
    assert predict_family_delta("lstm_sequence", params, features) == train_value


def test_parity_model_uses_nested_trained_params_not_aggregate_median() -> None:
    model = object.__new__(ParityChampionModelSet)
    artifact = {
        "model_name": "evt_cp_d1",
        "floor_delta": 0.40,
        "ceiling_delta": 0.40,
        "metrics": {"mae_spread": 1.0},
        "params": {
            "floor": {
                "global": 0.01,
                "table": {"v2:up": 0.02},
                "vol_cuts": [0.01, 0.03],
                "bins": 3,
            },
            "ceiling": {
                "global": 0.01,
                "table": {"v2:up": 0.03},
                "vol_cuts": [0.01, 0.03],
                "bins": 3,
            },
        },
    }
    row = {
        "close": 100.0,
        "atr_14": 2.0,
        "trend_context_m3": 1.0,
    }
    forecast = model._predict_classic_horizon(row, artifact, "d1")
    assert forecast is not None
    assert forecast.floor == 98.0
    assert forecast.ceiling == 103.0
    assert forecast.floor != 60.0
    assert forecast.ceiling != 140.0


def test_trained_nested_params_fail_closed_when_malformed() -> None:
    model = object.__new__(ParityChampionModelSet)
    artifact = {
        "model_name": "evt_cp_d1",
        "floor_delta": 0.02,
        "ceiling_delta": 0.03,
        "params": {
            "floor": {"table": {}, "vol_cuts": [0.01], "bins": 2},
            "ceiling": {"global": 0.03, "table": {}, "vol_cuts": [0.01], "bins": 2},
        },
    }
    with pytest.raises(ValueError, match="EVT params missing numeric global"):
        model._predict_classic_horizon(
            {"close": 100.0, "atr_14": 2.0, "trend_context_m3": 1.0},
            artifact,
            "d1",
        )


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


def _market(symbol: str = "AAPL", close: float = 100.0, sector: str = "Technology") -> dict:
    return {
        "symbol": symbol,
        "sector": sector,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1_000_000,
    }


def test_risk_gateway_collapses_same_side_horizons_and_caps_global_notional() -> None:
    signals = [
        {"symbol": "AAPL", "action": "BUY", "horizon": "d1", "confidence": 0.70, "rationale": "d1"},
        {"symbol": "AAPL", "action": "BUY", "horizon": "w1", "confidence": 0.90, "rationale": "w1"},
    ]
    result = approve_signal_batch(signals, [_market()], policy=_policy())
    assert len(result.orders) == 1
    order = result.orders[0]
    assert order["quantity"] == 500
    assert "qty" not in order
    assert order["metadata"]["approved_notional_usd"] == 50_000.0
    assert order["metadata"]["horizon"] == "w1"
    assert order["metadata"]["risk_action"] == "increase_exposure"
    assert any(item["reason"] == "lower_priority_same_symbol" for item in result.rejected)


def test_risk_gateway_rejects_cross_horizon_side_conflict() -> None:
    signals = [
        {"symbol": "AAPL", "action": "BUY", "horizon": "d1", "confidence": 0.9, "rationale": "up"},
        {"symbol": "AAPL", "action": "SELL", "horizon": "w1", "confidence": 0.8, "rationale": "down"},
    ]
    result = approve_signal_batch(signals, [_market()], policy=_policy())
    assert result.orders == []
    assert len(result.rejected) == 2
    assert {item["reason"] for item in result.rejected} == {"cross_horizon_side_conflict"}


def test_risk_gateway_kill_switches_on_stale_data_and_daily_loss() -> None:
    signal = {"symbol": "AAPL", "action": "BUY", "horizon": "d1", "confidence": 0.9, "rationale": "x"}
    stale = approve_signal_batch([signal], [_market()], policy=_policy(), market_data_fresh=False)
    assert stale.orders == []
    assert stale.rejected[0]["reason"] == "kill_switch: stale_data"

    loss = approve_signal_batch(
        [signal],
        [_market()],
        policy=_policy(),
        realized_pnl_usd=-15_001.0,
    )
    assert loss.orders == []
    assert loss.rejected[0]["reason"] == "kill_switch: daily_loss_limit"


def test_daily_loss_limit_allows_existing_position_to_be_closed() -> None:
    sell = {"symbol": "AAPL", "action": "SELL", "horizon": "d1", "confidence": 0.9, "rationale": "exit"}
    result = approve_signal_batch(
        [sell],
        [_market()],
        policy=_policy(),
        realized_pnl_usd=-20_000.0,
        existing_gross_notional_usd=50_000.0,
        existing_symbol_notional_usd={"AAPL": 50_000.0},
        existing_sector_notional_usd={"Technology": 50_000.0},
        existing_symbol_quantity={"AAPL": 500},
    )
    assert len(result.orders) == 1
    assert result.orders[0]["side"] == "SELL"
    assert result.orders[0]["quantity"] == 500
    assert result.orders[0]["metadata"]["risk_action"] == "decrease_exposure"


def test_live_cannot_enter_risk_gateway() -> None:
    signal = {"symbol": "AAPL", "action": "BUY", "horizon": "d1", "confidence": 0.9, "rationale": "x"}
    with pytest.raises(RuntimeError, match="LIVE execution is blocked"):
        approve_signal_batch(
            [signal],
            [_market()],
            policy=_policy(),
            live_trading_enabled=True,
        )


def test_stateful_paper_gateway_executes_only_risk_approved_quantity() -> None:
    gateway = PaperExecutionGateway(
        policy=_policy(),
        cost_config=CostModelConfig(commission_bps=0.0, slippage_bps=0.0),
    )
    buy = [
        {"symbol": "AAPL", "action": "BUY", "horizon": "d1", "confidence": 0.9, "rationale": "first"},
    ]
    first = gateway.run_cycle(
        cycle_id="c1",
        timestamp="2026-08-21T14:30:00+00:00",
        signals=buy,
        market_rows=[_market()],
    )
    assert first["approval"]["orders"][0]["quantity"] == 500
    assert first["execution"]["orders"][0]["quantity"] == 500
    assert first["execution"]["fills"][0]["quantity"] == 500

    second = gateway.run_cycle(
        cycle_id="c2",
        timestamp="2026-08-21T16:30:00+00:00",
        signals=buy,
        market_rows=[_market()],
    )
    assert second["approval"]["orders"] == []
    assert any(item["reason"] == "risk_capacity_exhausted" for item in second["approval"]["rejected"])

    gateway.executor.portfolio.realized_pnl = -20_000.0
    sell = [
        {"symbol": "AAPL", "action": "SELL", "horizon": "d1", "confidence": 0.95, "rationale": "defensive exit"},
    ]
    third = gateway.run_cycle(
        cycle_id="c3",
        timestamp="2026-08-21T18:30:00+00:00",
        signals=sell,
        market_rows=[_market()],
    )
    assert third["approval"]["orders"][0]["quantity"] == 500
    assert third["approval"]["orders"][0]["metadata"]["risk_action"] == "decrease_exposure"
    assert third["execution"]["fills"][0]["quantity"] == 500
    assert "AAPL" not in gateway.executor.portfolio.positions


def test_strategy_payloads_use_executor_quantity_contract() -> None:
    from strategies.base import StrategyDecision, build_order_payload

    decision = StrategyDecision(
        strategy_id="model_only",
        symbol="AAPL",
        side="BUY",
        score=0.8,
        qty=12,
        horizon="d1",
        entry_reason="entry",
        exit_reason="exit",
        stop_price=95.0,
        take_profit_price=110.0,
        expected_return=0.02,
        expected_range=15.0,
        timing_alignment=0.7,
    )
    payload = build_order_payload(
        decision,
        {"cooldown_cycles": 1, "max_rotation_per_cycle": 2},
        {"costs": {"commission_bps": 2.0, "slippage_bps": 3.0}},
    )
    assert payload["quantity"] == 12
    assert "qty" not in payload
