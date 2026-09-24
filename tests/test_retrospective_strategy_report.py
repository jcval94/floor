from __future__ import annotations

import json
from pathlib import Path

import pytest

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
        "requested_start_session": "2026-08-22",
        "requested_end_session": "2026-09-04",
        "requested_sessions": 11,
        "start_session": "2026-08-24",
        "end_session": "2026-09-04",
        "sessions": 10,
        "market_source": {
            "session_selection": {
                "requested_sessions": 11,
                "effective_sessions": 10,
                "incomplete_sessions": ["2026-08-22"],
                "selection_rule": "longest_contiguous_common_complete_daily_bar_run",
            }
        },
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
    assert report["requested_start_session"] == "2026-08-22"
    assert report["requested_end_session"] == "2026-09-04"
    assert report["requested_sessions"] == 11
    assert report["start_session"] == "2026-08-24"
    assert report["end_session"] == "2026-09-04"
    assert report["sessions"] == 10
    assert report["session_selection"]["incomplete_sessions"] == ["2026-08-22"]
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
    assert "Clasificación de la ventana completa" in page
    assert "El replay retrospectivo de hasta 3 meses es diagnóstico." in page
    assert "Clasificación de las 2 semanas" not in page
    assert "renderRetrospective" in script
    assert "data/strategy.json" in script
    assert "Torneo retrospectivo de NAV · ventana filtrada" in script
    assert "coverageDetail" in script
    assert "coverageNote" in script
    assert "bloque contiguo completo más largo" in script
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
    assert '"league_id": str(league_cfg["league_id"])' in runner


def test_tournament_workflow_uses_effective_league_id_for_attribution() -> None:
    workflow = (
        ROOT / ".github" / "workflows" / "capital_challenger_tournament.yml"
    ).read_text(encoding="utf-8")

    assert 'print(payload["league_id"])' in workflow
    assert '--history "$out/runs/$league_id/history.jsonl"' in workflow
    assert 'capital_tournament_$(echo' not in workflow
