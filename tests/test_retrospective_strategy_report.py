from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from replay.capital_tournament import _complete_market_sessions
from replay.publish_tournament_report import build_strategy_report, publish_tournament_report


ROOT = Path(__file__).resolve().parents[1]


def _row(strategy: str, ret: float, nav: float) -> dict:
    return {
        "strategy": strategy,
        "return": ret,
        "nav": nav,
        "sharpe": 1.0,
        "max_drawdown": -0.01,
        "trades": 2,
        "costs_paid": 3.5,
        "vs_spy": ret - 0.002,
        "equity_curve": [
            {"session": "2026-08-24", "nav": 10000.0},
            {"session": "2026-09-04", "nav": nav},
        ],
    }


def _payload() -> dict:
    return {
        "evidence_type": "retrospective_point_in_time_capital_tournament",
        "prospective_evidence": False,
        "future_data_used": False,
        "start_session": "2026-08-24",
        "end_session": "2026-09-04",
        "sessions": 10,
        "initial_nav_usd": 10000.0,
        "leaderboard": {
            "rows": [
                _row("weekly_opportunity_ridge", -0.01, 9900.0),
                _row("breakout_protected_by_floor", -0.02, 9800.0),
                _row("mean_reversion_floor_w1", 0.001, 10010.0),
                _row("cross_horizon_asymmetry", 0.003, 10030.0),
                _row("capital_allocation_challenger", 0.01, 10100.0),
                _row("benchmark_spy", 0.002, 10020.0),
                _row("benchmark_equal_weight", 0.0005, 10005.0),
            ]
        },
    }


def test_build_strategy_report_preserves_retrospective_contract() -> None:
    report = build_strategy_report(_payload())

    assert report["status"] == "RETROSPECTIVE_OK"
    assert report["prospective_evidence"] is False
    assert report["future_data_used"] is False
    assert report["start_session"] == "2026-08-24"
    assert report["end_session"] == "2026-09-04"
    assert report["sessions"] == 10
    assert len(report["rows"]) == 7
    assert report["rows"][0]["strategy"] == "capital_allocation_challenger"
    assert report["rows"][0]["rank"] == 1
    assert report["summary"]["challenger_rank"] == 1
    assert report["summary"]["challenger_vs_spy"] == pytest.approx(0.008)
    assert report["summary"]["best_base_strategy"] == "cross_horizon_asymmetry"
    assert report["summary"]["challenger_vs_best_base"] == pytest.approx(0.007)
    assert report["equity_curve"][-1]["equity"] == 10100.0
    assert report["equity_curve"][-1]["drawdown"] <= 0.0


def test_build_strategy_report_rejects_future_data() -> None:
    payload = _payload()
    payload["future_data_used"] = True
    with pytest.raises(ValueError, match="future_data_used"):
        build_strategy_report(payload)


def test_publish_tournament_report_writes_public_strategy_json(tmp_path: Path) -> None:
    source = tmp_path / "capital_tournament.json"
    source.write_text(json.dumps(_payload()), encoding="utf-8")
    output = tmp_path / "reports" / "strategy.json"

    report = publish_tournament_report(source, output)

    assert output.exists()
    loaded = json.loads(output.read_text(encoding="utf-8"))
    assert loaded["summary"] == report["summary"]
    assert "not prospective evidence" in loaded["methodology_note"]


def test_analytics_page_exposes_retrospective_tournament() -> None:
    page = (ROOT / "site" / "strategies.html").read_text(encoding="utf-8")
    script = (ROOT / "site" / "assets" / "league.js").read_text(encoding="utf-8")

    assert 'id="retrospective-tournament"' in page
    assert 'id="replayStatus"' in page
    assert 'id="replaySummary"' in page
    assert 'id="replayCompetitionChart"' in page
    assert 'id="replayChartMetrics"' in page
    assert 'id="replayWindow"' in page
    assert 'id="replayTable"' in page
    assert "Retrospective diagnostic · hasta 3 meses" in page
    assert "renderRetrospective" in script
    assert "data/strategy.json" in script
    assert "Torneo retrospectivo de NAV · ventana filtrada" in script
    assert "Diagnóstico" in script


def test_capital_tournament_uses_daily_close_history_for_long_windows() -> None:
    runner = (ROOT / "src" / "replay" / "capital_tournament.py").read_text(
        encoding="utf-8"
    )
    source = (ROOT / "src" / "replay" / "yahoo_source.py").read_text(
        encoding="utf-8"
    )

    assert "build_historical_close_feature_rows" in runner
    assert "fetch_replay_daily_market_data" in runner
    assert "build_point_in_time_feature_rows" not in runner
    assert "intraday_by_symbol" not in runner
    assert "def fetch_replay_daily_market_data" in source
    assert '"checkpoint_mode": "completed_daily_bar_at_close"' in source


def _daily_row(symbol: str, day: date) -> dict:
    return {
        "symbol": symbol,
        "timestamp": f"{day.isoformat()}T14:30:00+00:00",
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.5,
        "volume": 1000.0,
    }


def test_complete_market_sessions_skips_small_gaps_without_forward_fill() -> None:
    sessions = [date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 23)]
    daily = {
        "AAA": [_daily_row("AAA", day) for day in sessions if day != date(2026, 9, 22)],
        "SPY": [_daily_row("SPY", day) for day in sessions],
    }

    usable, skipped = _complete_market_sessions(sessions, daily, ["AAA"])

    assert usable == [date(2026, 9, 21), date(2026, 9, 23)]
    assert skipped == [
        {"session": "2026-09-22", "missing_symbols": ["AAA"]}
    ]


def test_complete_market_sessions_fails_closed_on_material_gaps() -> None:
    start = date(2026, 9, 1)
    sessions = [start + timedelta(days=index) for index in range(10)]
    missing = set(sessions[:3])
    daily = {
        "AAA": [_daily_row("AAA", day) for day in sessions if day not in missing],
        "SPY": [_daily_row("SPY", day) for day in sessions],
    }

    with pytest.raises(RuntimeError, match="market coverage too incomplete"):
        _complete_market_sessions(sessions, daily, ["AAA"])
