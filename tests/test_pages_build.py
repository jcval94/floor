from __future__ import annotations

import json
from pathlib import Path

from utils.pages_build import build_pages_data


def test_build_pages_data_generates_static_payloads(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    site_data = tmp_path / "site" / "data"
    (data_dir / "reports").mkdir(parents=True)
    (data_dir / "metrics").mkdir(parents=True)
    (data_dir / "training").mkdir(parents=True)

    (data_dir / "reports" / "dashboard.json").write_text(
        json.dumps(
            {
                "prediction_files": 1,
                "signal_files": 1,
                "latest_predictions": [
                    {
                        "symbol": "AAPL",
                        "horizon": "d1",
                        "floor_value": 100,
                        "ceiling_value": 110,
                        "model_version": "champion-v1",
                    }
                ],
                "api_key": "SHOULD_NOT_LEAK",
            }
        ),
        encoding="utf-8",
    )
    (data_dir / "metrics" / "public_metrics.json").write_text(json.dumps({"status": "ok", "series": []}), encoding="utf-8")
    (data_dir / "training" / "reviews.jsonl").write_text(
        json.dumps({"as_of": "2026-01-01", "model_name": "champion-v1", "action": "SKIP"}) + "\n",
        encoding="utf-8",
    )
    universe = tmp_path / "universe.yaml"
    universe.write_text("symbols:\n  - AAPL\n  - MSFT\n", encoding="utf-8")

    build_pages_data(data_dir=data_dir, site_data_dir=site_data, universe_path=universe)

    assert (site_data / "dashboard.json").exists()
    assert (site_data / "forecasts.json").exists()
    assert (site_data / "universe.json").exists()
    models = json.loads((site_data / "models.json").read_text(encoding="utf-8"))
    assert models["champion"] == "champion-v1"
    dashboard = json.loads((site_data / "dashboard.json").read_text(encoding="utf-8"))
    assert "api_key" not in json.dumps(dashboard)
