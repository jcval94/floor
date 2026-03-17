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
                        "model_version": "value:m3_value_linear@v2|timing:m3_timing_multiclass@v1",
                    }
                ],
                "api_key": "SHOULD_NOT_LEAK",
            }
        ),
        encoding="utf-8",
    )
    (data_dir / "metrics" / "public_metrics.json").write_text(json.dumps({"status": "ok", "series": []}), encoding="utf-8")
    (data_dir / "training" / "reviews.jsonl").write_text(
        json.dumps({"as_of": "2026-01-01", "model_name": "m3_value_linear", "model_key": "value", "recommendation": "SKIP_RETRAIN", "current_version": "v2"}) + "\n",
        encoding="utf-8",
    )
    (data_dir / "training" / "review_summary_latest.json").write_text(
        json.dumps(
            {
                "suite_version": "value:m3_value_linear@v2|timing:m3_timing_multiclass@v1",
                "models": {
                    "value": {"current_version": "v2"},
                    "timing": {"current_version": "v1"},
                },
            }
        ),
        encoding="utf-8",
    )
    universe = tmp_path / "universe.yaml"
    universe.write_text("symbols:\n  - AAPL\n  - MSFT\n", encoding="utf-8")

    build_pages_data(data_dir=data_dir, site_data_dir=site_data, universe_path=universe)

    assert (site_data / "dashboard.json").exists()
    assert (site_data / "forecasts.json").exists()
    assert (site_data / "universe.json").exists()
    models = json.loads((site_data / "models.json").read_text(encoding="utf-8"))
    assert models["champion"] == "value:m3_value_linear@v2|timing:m3_timing_multiclass@v1"
    assert set(models["champions"].keys()) == {"value", "timing"}
    dashboard = json.loads((site_data / "dashboard.json").read_text(encoding="utf-8"))
    assert "api_key" not in json.dumps(dashboard)


def test_build_pages_data_parses_nested_universe_yaml(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    site_data = tmp_path / "site" / "data"
    (data_dir / "reports").mkdir(parents=True)

    (data_dir / "reports" / "dashboard.json").write_text(
        json.dumps({"latest_predictions": []}),
        encoding="utf-8",
    )

    universe = tmp_path / "universe.yaml"
    universe.write_text(
        """
universe:
  name: us_top50_liquid_v1
  symbols:
    - aapl
    - msft
""".strip()
        + "\n",
        encoding="utf-8",
    )

    build_pages_data(data_dir=data_dir, site_data_dir=site_data, universe_path=universe)

    payload = json.loads((site_data / "universe.json").read_text(encoding="utf-8"))
    assert payload["symbols"] == ["AAPL", "MSFT"]


def test_build_pages_data_limits_top_opportunities_and_adds_relative_fields(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    site_data = tmp_path / "site" / "data"
    (data_dir / "reports").mkdir(parents=True)

    latest_predictions = []
    for i in range(12):
        latest_predictions.append(
            {
                "symbol": f"SYM{i}",
                "horizon": "d1",
                "floor_value": 100 + i,
                "ceiling_value": 110 + (2 * i),
                "floor_time_probability": 0.6,
                "ceiling_time_probability": 0.7,
            }
        )

    (data_dir / "reports" / "dashboard.json").write_text(
        json.dumps({"latest_predictions": latest_predictions}),
        encoding="utf-8",
    )

    universe = tmp_path / "universe.yaml"
    universe.write_text("""symbols:
  - AAPL
""", encoding="utf-8")

    build_pages_data(data_dir=data_dir, site_data_dir=site_data, universe_path=universe)

    forecasts = json.loads((site_data / "forecasts.json").read_text(encoding="utf-8"))
    opps = forecasts["top_opportunities"]
    assert len(opps) == 10
    assert "spread_relative" in opps[0]
    assert "spread_relative_pct" in opps[0]
    assert "opportunity_score" in opps[0]
    assert opps[0]["opportunity_score"] >= opps[-1]["opportunity_score"]


def test_build_pages_data_handles_git_lfs_pointer_inputs(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    site_data = tmp_path / "site" / "data"
    (data_dir / "reports").mkdir(parents=True)
    (data_dir / "training").mkdir(parents=True)

    lfs_pointer = """version https://git-lfs.github.com/spec/v1
oid sha256:deadbeef
size 42
"""
    (data_dir / "reports" / "dashboard.json").write_text(lfs_pointer, encoding="utf-8")
    (data_dir / "training" / "review_summary_latest.json").write_text(lfs_pointer, encoding="utf-8")
    (data_dir / "training" / "reviews.jsonl").write_text(lfs_pointer, encoding="utf-8")

    universe = tmp_path / "universe.yaml"
    universe.write_text("""symbols:
  - AAPL
""", encoding="utf-8")

    build_pages_data(data_dir=data_dir, site_data_dir=site_data, universe_path=universe)

    dashboard = json.loads((site_data / "dashboard.json").read_text(encoding="utf-8"))
    assert dashboard["prediction_files"] == 0
    assert dashboard["latest_predictions"] == []

    models = json.loads((site_data / "models.json").read_text(encoding="utf-8"))
    assert models["champion"] == "unknown"
    assert models["timeline"] == []


def test_build_pages_data_skips_invalid_jsonl_rows(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    site_data = tmp_path / "site" / "data"
    (data_dir / "reports").mkdir(parents=True)
    (data_dir / "training").mkdir(parents=True)

    (data_dir / "reports" / "dashboard.json").write_text(json.dumps({"latest_predictions": []}), encoding="utf-8")
    (data_dir / "training" / "reviews.jsonl").write_text(
        "not-json\n"
        + json.dumps(["not", "an", "object"])
        + "\n"
        + json.dumps({"as_of": "2026-01-02", "model_name": "m3"})
        + "\n",
        encoding="utf-8",
    )

    universe = tmp_path / "universe.yaml"
    universe.write_text("""symbols:
  - AAPL
""", encoding="utf-8")

    build_pages_data(data_dir=data_dir, site_data_dir=site_data, universe_path=universe)

    models = json.loads((site_data / "models.json").read_text(encoding="utf-8"))
    assert len(models["timeline"]) == 1
    assert models["timeline"][0]["model_name"] == "m3"
