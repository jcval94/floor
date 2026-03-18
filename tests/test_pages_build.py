from __future__ import annotations

import json
import sqlite3
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
    assert models["suite_status"] == "UNKNOWN"
    assert models["suite_recommendation"] == "PENDING"
    assert models["retraining_schedule"]["cadence_days"] == 14
    assert set(models["details"].keys()) == {"value", "timing"}
    assert models["details"]["value"]["current_version"] == "v2"
    dashboard = json.loads((site_data / "dashboard.json").read_text(encoding="utf-8"))
    assert "api_key" not in json.dumps(dashboard)


def test_build_pages_data_adds_retraining_countdown_from_summary_date(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    site_data = tmp_path / "site" / "data"
    (data_dir / "reports").mkdir(parents=True)
    (data_dir / "training").mkdir(parents=True)

    (data_dir / "reports" / "dashboard.json").write_text(json.dumps({"latest_predictions": []}), encoding="utf-8")
    (data_dir / "training" / "review_summary_latest.json").write_text(
        json.dumps({"as_of": "2026-01-10T00:00:00+00:00", "models": {}}),
        encoding="utf-8",
    )
    universe = tmp_path / "universe.yaml"
    universe.write_text("symbols:\n  - AAPL\n", encoding="utf-8")

    build_pages_data(data_dir=data_dir, site_data_dir=site_data, universe_path=universe)

    models = json.loads((site_data / "models.json").read_text(encoding="utf-8"))
    schedule = models["retraining_schedule"]
    assert schedule["cadence_days"] == 14
    assert schedule["last_review_at"] == "2026-01-10T00:00:00+00:00"
    assert schedule["next_review_at"] == "2026-01-24T00:00:00+00:00"
    assert isinstance(schedule["seconds_until_due"], int)
    assert isinstance(schedule["human_eta"], str)


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


def test_mirror_site_tree_copies_html_and_data(tmp_path: Path) -> None:
    from utils.pages_build import mirror_site_tree

    source = tmp_path / "site"
    target = tmp_path / "docs"
    (source / "data").mkdir(parents=True)
    (source / "assets").mkdir(parents=True)
    (source / "data" / "dashboard.json").write_text('{"status":"ok"}', encoding="utf-8")
    (source / "tickers.html").write_text('<html>tickers</html>', encoding="utf-8")
    (source / "assets" / "app.js").write_text('console.log("ok")', encoding="utf-8")

    mirror_site_tree(source, target)

    assert (target / "data" / "dashboard.json").read_text(encoding="utf-8") == '{"status":"ok"}'
    assert (target / "tickers.html").read_text(encoding="utf-8") == '<html>tickers</html>'
    assert (target / "assets" / "app.js").read_text(encoding="utf-8") == 'console.log("ok")'


def test_build_pages_data_includes_latest_intraday_and_latest_close(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    site_data = tmp_path / "site" / "data"
    (data_dir / "reports").mkdir(parents=True)
    (data_dir / "market").mkdir(parents=True)

    (data_dir / "reports" / "dashboard.json").write_text(
        json.dumps(
            {
                "latest_predictions": [
                    {"symbol": "AAPL", "horizon": "d1", "floor_value": 100, "ceiling_value": 110},
                    {"symbol": "MSFT", "horizon": "d1", "floor_value": 200, "ceiling_value": 220},
                ]
            }
        ),
        encoding="utf-8",
    )
    universe = tmp_path / "universe.yaml"
    universe.write_text("symbols:\n  - AAPL\n  - MSFT\n", encoding="utf-8")

    (data_dir / "training").mkdir(parents=True)
    (data_dir / "training" / "yahoo_market_rows.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"symbol": "AAPL", "timestamp": "2026-03-18T19:45:00+00:00", "close": 212.9}),
                json.dumps({"symbol": "AAPL", "timestamp": "2026-03-18T20:00:00+00:00", "close": 213.6}),
                json.dumps({"symbol": "MSFT", "timestamp": "2026-03-18T20:00:00+00:00", "close": 401.8}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    db_path = data_dir / "market" / "market_data.sqlite"
    with sqlite3.connect(db_path) as conn:
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
                source TEXT NOT NULL DEFAULT 'yahoo',
                fetched_at_utc TEXT NOT NULL,
                raw_payload TEXT,
                PRIMARY KEY (symbol, ts_utc)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO daily_bars(symbol, ts_utc, open, high, low, close, volume, source, fetched_at_utc, raw_payload)
            VALUES
                ('AAPL', '2026-03-17T20:00:00+00:00', 210, 212, 208, 211.5, 1000, 'yahoo', '2026-03-17T20:01:00+00:00', NULL),
                ('AAPL', '2026-03-18T20:00:00+00:00', 212, 214, 210, 213.25, 1000, 'yahoo', '2026-03-18T20:01:00+00:00', NULL),
                ('MSFT', '2026-03-18T20:00:00+00:00', 400, 405, 395, 402.1, 1000, 'yahoo', '2026-03-18T20:01:00+00:00', NULL)
            """
        )

    build_pages_data(data_dir=data_dir, site_data_dir=site_data, universe_path=universe)

    forecasts = json.loads((site_data / "forecasts.json").read_text(encoding="utf-8"))
    latest_close = forecasts["latest_close"]
    assert latest_close["AAPL"]["close"] == 213.25
    assert latest_close["AAPL"]["as_of"] == "2026-03-18T20:00:00+00:00"
    assert latest_close["MSFT"]["close"] == 402.1

    latest_intraday = forecasts["latest_intraday"]
    assert latest_intraday["AAPL"]["price"] == 213.6
    assert latest_intraday["AAPL"]["as_of"] == "2026-03-18T20:00:00+00:00"
    assert latest_intraday["MSFT"]["price"] == 401.8
