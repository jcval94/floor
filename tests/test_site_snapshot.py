from __future__ import annotations

import json
from pathlib import Path

from floor.reporting.generate_site_data import build_dashboard_snapshot


def test_build_dashboard_snapshot_keeps_all_prediction_files(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    pred_dir = data_dir / "predictions"
    signal_dir = data_dir / "signals"
    pred_dir.mkdir(parents=True)
    signal_dir.mkdir(parents=True)

    for idx in range(25):
        (pred_dir / f"s{idx}.jsonl").write_text(
            json.dumps(
                {
                    "symbol": f"S{idx}",
                    "horizon": "d1",
                    "as_of": "2026-01-01T12:00:00+00:00",
                    "floor_value": 100 + idx,
                    "ceiling_value": 110 + idx,
                }
            )
            + "\n",
            encoding="utf-8",
        )

    output = data_dir / "reports" / "dashboard.json"
    build_dashboard_snapshot(data_dir, output)

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["prediction_files"] == 25
    assert payload["prediction_count"] == 25
    assert len(payload["latest_predictions"]) == 25
    assert payload["source"] == "jsonl"


def test_build_dashboard_snapshot_prefers_sqlite_over_jsonl_discrepancy(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    (data_dir / "predictions").mkdir(parents=True)

    # Stale JSONL inventory (simulates versioned artifacts that should not win precedence).
    for idx in range(51):
        (data_dir / "predictions" / f"S{idx}.jsonl").write_text(
            json.dumps({"symbol": f"S{idx}", "horizon": "d1", "as_of": "2026-01-01T12:00:00+00:00"}) + "\n",
            encoding="utf-8",
        )

    db_dir = data_dir / "persistence"
    db_dir.mkdir(parents=True, exist_ok=True)
    from floor.storage import append_jsonl

    append_jsonl(
        data_dir / "predictions" / "AAPL_live.jsonl",
        {
            "symbol": "AAPL",
            "as_of": "2026-01-01T13:00:00+00:00",
            "event_type": "OPEN_PLUS_2H",
            "horizon": "d1",
            "floor_value": 101.0,
            "ceiling_value": 111.0,
            "model_version": "v1",
        },
    )
    append_jsonl(
        data_dir / "predictions" / "AAPL_live.jsonl",
        {
            "symbol": "AAPL",
            "as_of": "2026-01-01T14:00:00+00:00",
            "event_type": "OPEN_PLUS_4H",
            "horizon": "w1",
            "floor_value": 99.0,
            "ceiling_value": 115.0,
            "model_version": "v1",
        },
    )

    output = data_dir / "reports" / "dashboard.json"
    build_dashboard_snapshot(data_dir, output)
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert payload["source"] == "sqlite"
    assert payload["prediction_count"] == 2
    assert len(payload["latest_predictions"]) == 2


def test_build_dashboard_snapshot_does_not_overwrite_more_complete_existing_snapshot(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    (data_dir / "predictions").mkdir(parents=True)

    output = data_dir / "reports" / "dashboard.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "generated_at": "2026-01-01T12:00:00+00:00",
                "source": "sqlite",
                "prediction_count": 5,
                "signal_count": 4,
                "latest_predictions": [{"symbol": "AAPL", "horizon": "d1", "as_of": "2026-01-01T12:00:00+00:00"}],
                "validation": {"ok": True, "warnings": []},
            }
        ),
        encoding="utf-8",
    )

    # Only an empty fallback file is available now -> should not replace richer existing sqlite snapshot.
    (data_dir / "predictions" / "empty.jsonl").write_text("", encoding="utf-8")

    build_dashboard_snapshot(data_dir, output)
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert payload["source"] == "sqlite"
    assert payload["prediction_count"] == 5
    assert any("kept_previous_snapshot" in w for w in payload["validation"]["warnings"])
