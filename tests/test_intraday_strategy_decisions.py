from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from league import intraday_strategy_decisions as intraday
from league.run_eod import _retain_hold_positions
from strategies.common import geometry

ET = ZoneInfo("America/New_York")


def _market_row() -> dict:
    return {
        "symbol": "AAA",
        "sector": "Technology",
        "close": 100.0,
        "high": 101.0,
        "low": 99.0,
        "volume": 5_000_000,
        "avg_dollar_volume": 50_000_000.0,
        "momentum_10": 0.05,
        "momentum_20": 0.03,
        "rel_strength_20": 0.02,
        "trend_context_m3": 0.04,
        "drawdown_13w": -0.02,
        "atr_14": 2.0,
        "price_position_in_range_20": 0.55,
    }


def _forecast_row() -> dict:
    return {
        "symbol": "AAA",
        "model_version": "test",
        "floor_d1": 98.0,
        "ceiling_d1": 105.0,
        "risk_floor_d1": 90.0,
        "risk_ceiling_d1": 112.0,
        "risk_geometry_available_d1": True,
        "floor_w1": 98.0,
        "ceiling_w1": 105.0,
        "risk_floor_w1": 88.0,
        "risk_ceiling_w1": 114.0,
        "risk_geometry_available_w1": True,
        "floor_q1": 90.0,
        "ceiling_q1": 125.0,
        "risk_floor_q1": 70.0,
        "risk_ceiling_q1": 140.0,
        "risk_geometry_available_q1": True,
        "floor_m3": 80.0,
        "floor_week_m3": 8,
        "floor_week_m3_confidence": 0.8,
    }


def test_opportunity_rr_is_separate_from_calibrated_risk_rr() -> None:
    row = {**_market_row(), **_forecast_row()}
    current = geometry(row, "d1")

    assert current["opportunity_long_rr"] == pytest.approx(2.5)
    assert current["risk_long_rr"] == pytest.approx(0.5)
    # Backward-compatible alias remains a risk-envelope diagnostic.
    assert current["long_rr"] == pytest.approx(0.5)


def test_every_checkpoint_evaluates_all_strategy_families_and_preserves_gate_trace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    monkeypatch.setattr(intraday, "_validate_weekly_artifact", lambda *_args: None)
    monkeypatch.setattr(
        intraday,
        "predict_weekly_opportunity",
        lambda _row, _params: 1.0,
    )
    monkeypatch.setattr(intraday, "_current_positions", lambda *_args: {})

    payload = intraday.build_intraday_strategy_decisions(
        [_market_row()],
        [_forecast_row()],
        event_type="OPEN_PLUS_2H",
        as_of=datetime(2026, 10, 1, 11, 30, tzinfo=ET),
        data_dir=tmp_path / "data",
        input_snapshot_id="a" * 64,
        strategies_config_path=repo_root / "config" / "strategies.yaml",
        league_config_path=repo_root / "config" / "strategy_league.json",
        weekly_artifact={"params": {"canonical_serving_enabled": False}},
        checkpoint_quotes={
            "AAA": {
                "price": 101.0,
                "previous_close": 100.0,
                "as_of": "2026-10-01T15:25:00+00:00",
                "source": "yahoo_chart_checkpoint",
                "session_return": 0.01,
                "return_15m": 0.004,
                "return_1h": 0.008,
                "session_high": 101.2,
                "session_low": 99.5,
                "completed_bars": 24,
            },
            "SPY": {
                "price": 505.0,
                "previous_close": 500.0,
                "as_of": "2026-10-01T15:25:00+00:00",
                "source": "yahoo_chart_checkpoint",
                "session_return": 0.01,
            },
        },
    )

    assert payload["mode"] == "shadow_observation_no_execution"
    assert payload["live_execution_enabled"] is False
    assert payload["orders_emitted"] is False
    assert payload["summary"]["strategies_evaluated"] == 4
    assert payload["summary"]["decisions_evaluated"] == 4
    assert payload["summary"]["actionable_decisions"] >= 3

    breakout = payload["strategies"]["breakout_protected_by_floor"]["decisions"][0]
    assert breakout["action"] == "BUY"
    trace = breakout["decision_trace"]
    assert trace["inputs"]["opportunity_long_rr"] == pytest.approx(4.0 / 3.0)
    assert trace["inputs"]["risk_long_rr"] == pytest.approx(4.0 / 11.0)
    assert trace["intraday"]["price"] == pytest.approx(101.0)
    assert trace["intraday"]["return_15m"] == pytest.approx(0.004)
    timing = trace["intraday_timing"]
    assert timing["status"] == "confirmed"
    assert timing["action_changed"] is False
    assert timing["qty_changed"] is False
    assert breakout["intraday_rank_score"] > breakout["base_score"]
    assert payload["intraday_timing_adapter"]["action_changes"] == 0
    assert payload["intraday_timing_adapter"]["qty_changes"] == 0
    assert payload["summary"]["quote_coverage"] == pytest.approx(1.0)
    assert trace["gates"]["long_opportunity_rr"] is True
    assert trace["gates"]["long_alpha"] is True
    assert trace["gates"]["long_payoff_after_costs"] is True

    mean_reversion = payload["strategies"]["mean_reversion_floor_w1"]["decisions"][0]
    assert mean_reversion["action"] == "BUY"
    assert mean_reversion["decision_trace"]["gates"]["long_near_floor"] is True
    assert mean_reversion["decision_trace"]["gates"]["long_reversal"] is True

    assert payload["capital_allocation_challenger"]["target_count"] >= 1


def test_momentum_timing_adapter_rewards_confirmation_and_penalizes_conflict() -> None:
    decision = SimpleNamespace(side="BUY", score=1.0)
    confirmed = intraday._timing_context(
        "breakout_protected_by_floor",
        decision,
        {"intraday_return_15m": 0.004, "intraday_return_1h": 0.010},
    )
    contradicted = intraday._timing_context(
        "breakout_protected_by_floor",
        decision,
        {"intraday_return_15m": -0.004, "intraday_return_1h": -0.010},
    )

    assert confirmed["status"] == "confirmed"
    assert confirmed["rank_score"] > confirmed["base_score"]
    assert contradicted["status"] == "contradicted"
    assert contradicted["rank_score"] < contradicted["base_score"]
    assert confirmed["action_changed"] is False
    assert contradicted["qty_changed"] is False


def test_mean_reversion_timing_prefers_short_term_reversal_after_one_hour_dislocation() -> None:
    decision = SimpleNamespace(side="BUY", score=1.0)
    reversal = intraday._timing_context(
        "mean_reversion_floor_w1",
        decision,
        {"intraday_return_15m": 0.004, "intraday_return_1h": -0.012},
    )
    catching_fall = intraday._timing_context(
        "mean_reversion_floor_w1",
        decision,
        {"intraday_return_15m": -0.004, "intraday_return_1h": -0.012},
    )

    assert reversal["status"] == "confirmed"
    assert reversal["rank_score"] > 1.0
    assert catching_fall["rank_score"] < reversal["rank_score"]
    assert reversal["mode"] == "ranking_only"


def test_open_without_intraday_returns_keeps_source_score_unchanged() -> None:
    decision = SimpleNamespace(side="BUY", score=0.75)
    timing = intraday._timing_context(
        "weekly_opportunity_ridge",
        decision,
        {"intraday_return_15m": None, "intraday_return_1h": None},
    )

    assert timing["status"] == "unavailable"
    assert timing["multiplier"] == pytest.approx(1.0)
    assert timing["rank_score"] == pytest.approx(0.75)
    assert timing["action_changed"] is False
    assert timing["qty_changed"] is False


def test_checkpoint_decisions_are_written_idempotently_by_session_and_event(
    tmp_path: Path,
) -> None:
    payload = {
        "session_day": "2026-10-01",
        "event": "OPEN",
        "summary": {"decisions_evaluated": 200},
    }

    first = intraday.write_intraday_strategy_decisions(
        payload,
        data_dir=tmp_path / "data",
    )
    second = intraday.write_intraday_strategy_decisions(
        {**payload, "summary": {"decisions_evaluated": 204}},
        data_dir=tmp_path / "data",
    )

    assert first == second
    assert '"decisions_evaluated": 204' in first.read_text(encoding="utf-8")
    latest = (
        tmp_path
        / "data"
        / "metrics"
        / "strategy_decisions"
        / "intraday"
        / "latest.json"
    )
    assert latest.is_file()
    assert latest.read_text(encoding="utf-8") == first.read_text(encoding="utf-8")


def test_daily_entry_discovery_retains_hold_positions_without_exceeding_capacity() -> None:
    strategies_cfg = {
        "portfolio": {"nav_usd": 10_000},
    }
    current_positions = {
        "AAA": {
            "qty": 20,
            "stop_price": 90.0,
            "take_profit_price": 110.0,
        }
    }
    rows = {
        "AAA": {"close": 100.0},
        "BBB": {"close": 100.0},
    }
    decisions = [
        SimpleNamespace(
            symbol="AAA",
            side="HOLD",
            score=0.0,
            expected_return=0.0,
        ),
        SimpleNamespace(
            symbol="BBB",
            side="BUY",
            score=2.0,
            expected_return=0.05,
        ),
    ]
    targets = {
        "BBB": {
            "weight": 0.90,
            "stop_price": 92.0,
            "take_profit_price": 115.0,
            "score": 2.0,
        }
    }

    result = _retain_hold_positions(
        targets,
        decisions,
        current_positions,
        rows,
        strategies_cfg,
    )

    assert result["AAA"]["weight"] == pytest.approx(0.20)
    assert result["AAA"]["retained_on_hold"] is True
    assert result["BBB"]["weight"] == pytest.approx(0.80)
    assert sum(float(spec["weight"]) for spec in result.values()) <= 1.0 + 1e-12


def test_explicit_sell_is_not_retained_by_daily_entry_discovery() -> None:
    result = _retain_hold_positions(
        {},
        [
            SimpleNamespace(
                symbol="AAA",
                side="SELL",
                score=1.0,
                expected_return=-0.02,
            )
        ],
        {
            "AAA": {
                "qty": 10,
                "stop_price": 90.0,
                "take_profit_price": 110.0,
            }
        },
        {"AAA": {"close": 100.0}},
        {"portfolio": {"nav_usd": 10_000}},
    )

    assert "AAA" not in result
