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
    for event in [
        "OPEN",
        "OPEN_PLUS_2H",
        "OPEN_PLUS_4H",
        "OPEN_PLUS_6H",
        "CLOSE",
        "drift_alert",
        "retrain_decision",
        "incident_alert",
    ]:
        msg = build_message(
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
            )
        )
        assert event in msg
        assert "Top picks" in msg
        assert "Techo esperado" in msg


def test_notifiers_primary_and_secondary_channels(monkeypatch) -> None:
    def _fake_urlopen(req, timeout=10):
        return _FakeResp()

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)

    tele = TelegramNotifier("t", "c1", "c2")
    ntfy = NtfyNotifier("https://ntfy.sh", "main", "backup")
    resend = ResendNotifier("k", "ops@test.com", "a@test.com", "b@test.com")

    assert len(tele.send("hello")) == 2
    assert len(ntfy.send("hello")) == 2
    assert len(resend.send("subject", "hello")) == 2


def test_history_reports_and_pages_export(tmp_path: Path) -> None:
    hw = HistoryWriter(str(tmp_path / "history"))
    payload = {"date": "2026-03-12", "metric": "pnl", "value": 1.23}

    first = hw.write_snapshot("workflow_runs", "2026-03-12", "OPEN", payload)
    second = hw.write_snapshot("workflow_runs", "2026-03-12", "OPEN", payload)
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

    d_path = hw.write_daily_summary("2026-03-12", daily)
    w_path = hw.write_weekly_summary("2026-W11", weekly)
    assert Path(d_path).exists()
    assert Path(w_path).exists()
    assert model["status"] == "ok"

    result = export_pages_data(
        output_dir=str(tmp_path / "pages"),
        date_partition="2026-03-12",
        datasets={
            "dashboard_overview": [{"date": "2026-03-12", "value": 10, "secret": "x"}],
            "ticker_detail": [{"ticker": "AAA", "metric": "score", "value": 0.8}],
            "model_health": [{"metric": "auc", "value": 0.7}],
            "strategy_performance": [{"strategy": "s1", "pnl": 2.0}],
            "retrain_history": [{"date": "2026-03-12", "retrain": "no"}],
            "incident_log": [{"incident": "api timeout", "severity": "medium"}],
        },
    )

    exported_json = Path(result["datasets"]["dashboard_overview"]["latest_json"])
    records = json.loads(exported_json.read_text(encoding="utf-8"))
    assert "secret" not in records[0]
    assert Path(result["datasets"]["ticker_detail"]["historical_csv"]).exists()
