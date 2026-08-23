from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from floor.calendar import is_us_market_holiday
from floor.storage import append_jsonl
from forecasting.generate_forecasts import _blended_confidence
from forecasting.merge_ai_signal import merge_market_with_ai_signal
from monitoring.health_snapshot import build_health_snapshot
from utils.market_data_guard import validate_market_data_freshness


def _seed_market_db(path: Path, rows: list[tuple[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE daily_bars (
                symbol TEXT NOT NULL,
                ts_utc TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                PRIMARY KEY (symbol, ts_utc)
            )
            """
        )
        conn.executemany(
            "INSERT INTO daily_bars(symbol, ts_utc, open, high, low, close, volume) VALUES(?, ?, 1, 1, 1, 1, 1)",
            rows,
        )


def test_missing_ai_is_neutral_and_cannot_boost_confidence() -> None:
    merged = merge_market_with_ai_signal({"symbol": "AAPL", "close": 100.0}, None)
    assert merged["ai_present"] is False
    assert merged["ai_weight"] == 0.0
    assert merged["ai_effective_score"] == 0.0
    assert _blended_confidence(0.72, False, 1.0, 1.0) == pytest.approx(0.72)


def test_market_freshness_uses_market_sessions_over_weekend(tmp_path: Path) -> None:
    # Monday pre-open: Friday is the latest completed session, not "3 days stale".
    now = datetime(2026, 8, 24, 14, 0, tzinfo=timezone.utc)
    db = tmp_path / "market.sqlite"
    _seed_market_db(db, [("AAPL", "2026-08-21T20:00:00+00:00")])
    result = validate_market_data_freshness(
        db,
        ["AAPL"],
        now=now,
        max_stale_sessions=0,
    )
    assert result["required_latest_session"] == "2026-08-21"
    assert result["status"] == "OK"


def test_market_freshness_rejects_one_missed_session(tmp_path: Path) -> None:
    now = datetime(2026, 8, 24, 14, 0, tzinfo=timezone.utc)
    db = tmp_path / "market.sqlite"
    _seed_market_db(db, [("AAPL", "2026-08-20T20:00:00+00:00")])
    with pytest.raises(RuntimeError, match="AAPL:1sessions"):
        validate_market_data_freshness(
            db,
            ["AAPL"],
            now=now,
            max_stale_sessions=0,
        )


def test_market_calendar_includes_good_friday_and_juneteenth() -> None:
    assert is_us_market_holiday(date(2026, 4, 3)) is True
    assert is_us_market_holiday(date(2026, 6, 19)) is True


def _write_dashboard_and_review(data_dir: Path, now: datetime) -> None:
    dashboard = data_dir / "reports" / "dashboard.json"
    dashboard.parent.mkdir(parents=True, exist_ok=True)
    dashboard.write_text(
        json.dumps(
            {
                "generated_at": now.isoformat(),
                "latest_predictions": [
                    {"symbol": "AAPL", "as_of": now.isoformat(), "event_type": "OPEN", "horizon": "d1"}
                ],
            }
        ),
        encoding="utf-8",
    )
    review = data_dir / "training" / "review_summary_latest.json"
    review.parent.mkdir(parents=True, exist_ok=True)
    review.write_text(
        json.dumps({"suite_status": "OK", "suite_recommendation": "KEEP"}),
        encoding="utf-8",
    )


def test_health_is_critical_when_newest_universe_batch_is_partial(tmp_path: Path) -> None:
    now = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)  # Saturday -> no checkpoint noise
    _write_dashboard_and_review(tmp_path, now)
    universe = tmp_path / "universe.yaml"
    universe.write_text("symbols:\n  - AAPL\n  - MSFT\n", encoding="utf-8")
    for symbol in ("AAPL", "MSFT"):
        for horizon in ("d1", "w1", "q1", "m3"):
            append_jsonl(
                tmp_path / "predictions" / f"{symbol}.jsonl",
                {"symbol": symbol, "as_of": "2026-08-22T10:00:00+00:00", "event_type": "OPEN", "horizon": horizon},
            )
    # Newer partial batch must take precedence over the older complete one.
    for horizon in ("d1", "w1", "q1"):
        append_jsonl(
            tmp_path / "predictions" / "AAPL.jsonl",
            {"symbol": "AAPL", "as_of": "2026-08-22T11:00:00+00:00", "event_type": "OPEN_PLUS_2H", "horizon": horizon},
        )

    health = build_health_snapshot(tmp_path, now=now, universe_path=universe)
    batch = next(item for item in health["series"] if item["name"] == "prediction_batch_completeness")
    assert health["status"] == "CRITICAL"
    assert batch["status"] == "CRITICAL"
    assert "incomplete" in batch["detail"]


def test_training_workflow_never_commits_heavy_reconstructable_artifacts() -> None:
    root = Path(__file__).resolve().parents[1]
    text = (root / ".github" / "workflows" / "manual_train_all_models.yml").read_text(encoding="utf-8")
    assert "commit_champions:" in text
    commit_block = text.split("- name: Commit lightweight champions only", 1)[1].split("- name: Upload full training run artifacts", 1)[0]
    for forbidden in (
        "data/market",
        "data/persistence",
        "modelable_dataset",
        "yahoo_market_rows",
        "_champion.pkl",
        "manual_train_all_models_*.log",
        "chunk_binary_file",
    ):
        assert forbidden not in commit_block
    assert "data/training/models/value_champion.json" in commit_block
    assert "data/training/models/timing_champion.json" in commit_block
