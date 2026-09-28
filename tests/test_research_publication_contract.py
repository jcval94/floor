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



def test_pages_publish_realized_operations_payload(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    league_id = "strategy_league_ops"
    history = (
        data_dir
        / "metrics"
        / "strategy_league"
        / "runs"
        / league_id
        / "history.jsonl"
    )
    history.parent.mkdir(parents=True)
    records = [
        {
            "session": "2026-01-01",
            "trades": [],
            "decisions_for_next_open": {},
            "state_after": {"members": {}},
        },
        {
            "session": "2026-01-02",
            "trades": [
                {
                    "member": "alpha_strategy",
                    "symbol": "AAA",
                    "side": "BUY",
                    "qty": 10,
                    "fill_price": 100.0,
                    "costs": 1.0,
                }
            ],
            "decisions_for_next_open": {},
            "state_after": {"members": {}},
        },
        {
            "session": "2026-01-03",
            "trades": [
                {
                    "member": "alpha_strategy",
                    "symbol": "AAA",
                    "side": "SELL",
                    "qty": 10,
                    "fill_price": 105.0,
                    "costs": 1.0,
                }
            ],
            "decisions_for_next_open": {},
            "state_after": {"members": {}},
        },
    ]
    history.write_text("\n".join(json.dumps(row) for row in records) + "\n", encoding="utf-8")

    league_config = tmp_path / "league.json"
    league_config.write_text(
        json.dumps(
            {
                "league_id": league_id,
                "members": [
                    {"id": "alpha_strategy", "type": "strategy"},
                    {"id": "benchmark_spy", "type": "benchmark"},
                ],
            }
        ),
        encoding="utf-8",
    )

    site = tmp_path / "site" / "data"
    result = publish_research_payloads(
        data_dir=data_dir,
        site_data_dir=site,
        league_config_path=league_config,
    )

    operations = json.loads(
        (site / "strategy_league_operations.json").read_text(encoding="utf-8")
    )
    assert result["prospective_operations_status"] == "OK"
    assert operations["prospective_evidence"] is True
    assert operations["realized_operations"] == 1
    assert operations["top_operations"][0]["strategy"] == "alpha_strategy"
    assert operations["bottom_operations"] == []
