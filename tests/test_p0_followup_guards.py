from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from floor.storage import append_jsonl
from floor.training.governance import block_automatic_retrain
from utils.prediction_batch_guard import validate_latest_prediction_batch
from utils.training_data_guard import validate_training_market_coverage


def _write_batch(
    data_dir: Path,
    *,
    as_of: str,
    symbols: list[str],
    horizons: list[str],
    event_type: str = "OPEN",
) -> None:
    for symbol in symbols:
        for horizon in horizons:
            append_jsonl(
                data_dir / "predictions" / f"{symbol}.jsonl",
                {
                    "symbol": symbol,
                    "as_of": as_of,
                    "event_type": event_type,
                    "horizon": horizon,
                },
            )


def test_complete_prediction_batch_passes(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_batch(
        data_dir,
        as_of="2026-08-21T14:30:00+00:00",
        symbols=["AAPL", "MSFT"],
        horizons=["d1", "w1", "q1", "m3"],
    )

    result = validate_latest_prediction_batch(
        data_dir,
        ["AAPL", "MSFT"],
        event_type="OPEN",
    )

    assert result["status"] == "OK"
    assert result["expected_pairs"] == 8
    assert result["observed_pairs"] == 8


def test_partial_latest_prediction_batch_fails_even_if_history_was_complete(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_batch(
        data_dir,
        as_of="2026-08-21T14:30:00+00:00",
        symbols=["AAPL", "MSFT"],
        horizons=["d1", "w1", "q1", "m3"],
    )
    _write_batch(
        data_dir,
        as_of="2026-08-21T16:30:00+00:00",
        symbols=["AAPL"],
        horizons=["d1", "w1", "q1", "m3"],
        event_type="OPEN_PLUS_2H",
    )
    _write_batch(
        data_dir,
        as_of="2026-08-21T18:30:00+00:00",
        symbols=["AAPL"],
        horizons=["d1", "w1", "q1"],
    )

    with pytest.raises(RuntimeError, match=r"incomplete latest batch"):
        validate_latest_prediction_batch(
            data_dir,
            ["AAPL", "MSFT"],
            event_type="OPEN",
        )


def test_retraining_governance_preserves_diagnosis_but_clears_auto_tasks(tmp_path: Path) -> None:
    summary = tmp_path / "review_summary_latest.json"
    summary.write_text(
        json.dumps(
            {
                "suite_status": "ALERT",
                "suite_recommendation": "RETRAIN_NOW",
                "tasks_for_auto_retrain": ["value", "timing"],
                "models": {
                    "value": {"recommendation": "RETRAIN_NOW", "auto_retrain": True},
                    "timing": {"recommendation": "RETRAIN_NOW", "auto_retrain": True},
                },
            }
        ),
        encoding="utf-8",
    )

    payload = block_automatic_retrain(summary)

    assert payload["suite_recommendation"] == "RETRAIN_NOW"
    assert payload["tasks_for_auto_retrain_requested"] == ["value", "timing"]
    assert payload["tasks_for_auto_retrain"] == []
    assert payload["auto_retrain_enabled"] is False
    assert payload["models"]["value"]["auto_retrain_requested"] is True
    assert payload["models"]["value"]["auto_retrain"] is False


def _seed_training_db(path: Path, rows_by_symbol: dict[str, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(tz=timezone.utc).isoformat()
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
        for symbol, count in rows_by_symbol.items():
            for index in range(count):
                conn.execute(
                    """
                    INSERT INTO daily_bars(symbol, ts_utc, open, high, low, close, volume)
                    VALUES(?, ?, 1, 1, 1, 1, 1)
                    """,
                    (symbol, f"{now}|{index:03d}" if index else now),
                )


def test_training_coverage_rejects_missing_or_thin_symbols(tmp_path: Path) -> None:
    universe = tmp_path / "universe.yaml"
    universe.write_text("universe:\n  symbols:\n    - AAPL\n    - MSFT\n", encoding="utf-8")
    db = tmp_path / "market.sqlite"
    _seed_training_db(db, {"AAPL": 2, "MSFT": 1})

    with pytest.raises(RuntimeError, match=r"insufficient per-symbol history"):
        validate_training_market_coverage(
            db,
            universe,
            benchmark="",
            min_rows_per_symbol=2,
            max_age_days=7,
        )
