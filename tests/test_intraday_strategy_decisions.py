from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from league import intraday_strategy_decisions as intraday
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
    assert trace["inputs"]["opportunity_long_rr"] == pytest.approx(2.5)
    assert trace["inputs"]["risk_long_rr"] == pytest.approx(0.5)
    assert trace["gates"]["long_opportunity_rr"] is True
    assert trace["gates"]["long_alpha"] is True
    assert trace["gates"]["long_payoff_after_costs"] is True

    mean_reversion = payload["strategies"]["mean_reversion_floor_w1"]["decisions"][0]
    assert mean_reversion["action"] == "BUY"
    assert mean_reversion["decision_trace"]["gates"]["long_near_floor"] is True
    assert mean_reversion["decision_trace"]["gates"]["long_reversal"] is True

    assert payload["capital_allocation_challenger"]["target_count"] >= 1


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
