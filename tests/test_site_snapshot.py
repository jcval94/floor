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
            json.dumps({"symbol": f"S{idx}", "horizon": "d1", "floor_value": 100 + idx, "ceiling_value": 110 + idx}) + "\n",
            encoding="utf-8",
        )

    output = data_dir / "reports" / "dashboard.json"
    build_dashboard_snapshot(data_dir, output)

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["prediction_files"] == 25
    assert len(payload["latest_predictions"]) == 25
