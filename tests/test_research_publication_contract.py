from __future__ import annotations

import json
from pathlib import Path

from replay.publish_research_site import publish_research_payloads


def _league_config(path: Path) -> Path:
    path.write_text(
        json.dumps({"league_id": "strategy_league_v8_net_alpha_10k"}),
        encoding="utf-8",
    )
    return path


def test_pages_withholds_legacy_reset_fold_walk_forward(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    reports = data_dir / "reports"
    reports.mkdir(parents=True)
    (reports / "walk_forward_oos.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "MODEL_OOS_OK",
                "start_session": "2026-03-02",
                "end_session": "2026-09-04",
                "sessions": 131,
                "folds": 7,
                "rows": [{"strategy": "benchmark_spy", "return": 0.10}],
            }
        ),
        encoding="utf-8",
    )

    site = tmp_path / "site" / "data"
    publish_research_payloads(
        data_dir=data_dir,
        site_data_dir=site,
        league_config_path=_league_config(tmp_path / "league.json"),
    )

    published = json.loads(
        (site / "walk_forward_oos.json").read_text(encoding="utf-8")
    )
    assert published["status"] == "WAITING_FOR_CONTINUOUS_RECALCULATION"
    assert published["rows"] == []
    assert published["legacy_artifact"]["start_session"] == "2026-03-02"
    assert "reset" in published["legacy_artifact"]["reason"]


def test_pages_accepts_v2_continuous_walk_forward(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    reports = data_dir / "reports"
    reports.mkdir(parents=True)
    source = {
        "schema_version": 2,
        "status": "MODEL_OOS_OK",
        "portfolio_continuity_across_folds": True,
        "fold_liquidations": False,
        "start_session": "2026-03-02",
        "end_session": "2026-09-23",
        "sessions": 145,
        "folds": 7,
        "rows": [{"strategy": "benchmark_spy", "return": 0.10}],
        "fold_reports": [],
    }
    (reports / "walk_forward_oos.json").write_text(
        json.dumps(source),
        encoding="utf-8",
    )

    site = tmp_path / "site" / "data"
    publish_research_payloads(
        data_dir=data_dir,
        site_data_dir=site,
        league_config_path=_league_config(tmp_path / "league.json"),
    )

    published = json.loads(
        (site / "walk_forward_oos.json").read_text(encoding="utf-8")
    )
    assert published == source
