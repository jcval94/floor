from __future__ import annotations

import json
from pathlib import Path

from notifications.message_builder import MessageContext, build_message
from notifications.ntfy_notifier import NtfyNotifier
from notifications.resend_notifier import ResendNotifier
from notifications.telegram_notifier import TelegramNotifier
from reporting.daily_report import generate_daily_report
from reporting.model_report import generate_model_report
from reporting.weekly_report import generate_weekly_report
from storage.export_pages_data import export_pages_data
from storage.history_writer import HistoryWriter


class _FakeResp:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def read(self):
        return b'{"ok": true}'


def test_message_builder_for_required_events() -> None:
    required_events = [
        "OPEN",
        "OPEN_PLUS_2H",
        "OPEN_PLUS_4H",
        "OPEN_PLUS_6H",
        "CLOSE",
        "drift_alert",
        "retrain_decision",
        "incident_alert",
    ]
    for event in required_events:
        message = build_message(
            MessageContext(
                event=event,
                date="2026-03-12",
                top_picks=["AAA", "BBB"],
                top_blocks=["CCC"],
                expected_floor="100",
                expected_ceiling="110",
                expected_bucket_or_business_day="OPEN_PLUS_4H",
                reward_risk="2.1",
                strategy_action="reduce exposure",
                risk_changes_and_actions="VaR up 10%, de-risked 20%",
                floor_m3="170.5",
                floor_week_m3="2",
                floor_week_m3_start_date="2026-03-23",
                floor_week_m3_end_date="2026-03-27",
                floor_week_m3_confidence="0.62",
                m3_material_change="yes",
                m3_week_proximity="cerca",
            )
        )
        assert f"[{event}]" in message
        assert "Top picks" in message
        assert "Top blocks" in message
        assert "Piso esperado" in message
        assert "Techo esperado" in message
        assert "Bucket/día hábil esperado" in message
        assert "Reward/Risk" in message
        assert "Acción recomendada por estrategia" in message
        assert "Cambios de riesgo y acciones tomadas" in message
        assert "floor_m3" in message
        assert "floor_week_m3" in message
        assert "floor_week_m3_start_date" in message
        assert "floor_week_m3_end_date" in message
        assert "floor_week_m3_confidence" in message
        assert "m3_material_change" in message
        assert "m3_week_proximity" in message


def test_notifiers_primary_and_secondary_channels(monkeypatch) -> None:
    def _fake_urlopen(req, timeout=10):
        return _FakeResp()

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)

    telegram = TelegramNotifier("token", "main", "secondary")
    ntfy = NtfyNotifier("https://ntfy.sh", "main", "secondary")
    resend = ResendNotifier("api-key", "ops@test.com", "main@test.com", "secondary@test.com")

    assert len(telegram.send("hello")) == 2
    assert len(ntfy.send("hello")) == 2
    assert len(resend.send("subject", "hello")) == 2


def test_history_reports_and_pages_export(tmp_path: Path) -> None:
    writer = HistoryWriter(str(tmp_path / "history"))
    payload = {"date": "2026-03-12", "metric": "pnl", "value": 1.23}

    first = writer.write_snapshot("workflow_runs", "2026-03-12", "OPEN", payload)
    second = writer.write_snapshot("workflow_runs", "2026-03-12", "OPEN", payload)
    assert first["written"] is True
    assert second["written"] is False

    daily = generate_daily_report(
        "2026-03-12",
        session_metrics={"CLOSE": {"pnl": 100, "win_rate": 0.6, "max_drawdown": -0.1}},
        risk_changes=[{"change": "reduced gross"}],
        incidents=[{"incident": "none", "severity": "low"}],
    )
    weekly = generate_weekly_report("2026-W11", [daily])
    model = generate_model_report("2026-03-12", {"auc": 0.7}, [], [{"retrain": "no"}])

    daily_path = writer.write_daily_summary("2026-03-12", daily)
    weekly_path = writer.write_weekly_summary("2026-W11", weekly)
    assert Path(daily_path).exists()
    assert Path(weekly_path).exists()
    assert model["status"] == "ok"

    result = export_pages_data(
        output_dir=str(tmp_path / "pages"),
        date_partition="2026/03/12",
        datasets={
            "dashboard_overview": [{"date": "2026-03-12", "value": 10, "secret": "x"}],
            "ticker_detail": [{"ticker": "AAA", "metric": "score", "value": 0.8, "floor_m3": 170.5, "floor_week_m3": 2, "floor_week_m3_confidence": 0.62, "floor_week_m3_top3": [{"week":2,"probability":0.62}], "secret": "y"}],
            "model_health": [{"metric": "auc", "value": 0.7}],
            "strategy_performance": [{"strategy": "s1", "pnl": 2.0}],
            "retrain_history": [{"date": "2026-03-12", "retrain": "no"}],
            "incident_log": [{"incident": "api timeout", "severity": "medium"}],
        },
    )

    latest_dashboard = Path(result["datasets"]["dashboard_overview"]["latest_json"])
    records = json.loads(latest_dashboard.read_text(encoding="utf-8"))
    assert "secret" not in records[0]
    ticker_records = json.loads(Path(result["datasets"]["ticker_detail"]["latest_json"]).read_text(encoding="utf-8"))
    assert "floor_m3" in ticker_records[0]
    assert "floor_week_m3_top3" in ticker_records[0]
    assert "secret" not in ticker_records[0]
    assert Path(result["datasets"]["ticker_detail"]["historical_csv"]).exists()
