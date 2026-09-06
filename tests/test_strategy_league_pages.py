from __future__ import annotations

import json
from pathlib import Path

import pytest

from league.publish_site import publish_league_payload


ROOT = Path(__file__).resolve().parents[1]


def test_publish_league_payload_ranks_and_summarizes_competition(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    source = data_dir / "metrics" / "strategy_league" / "leaderboard.json"
    source.parent.mkdir(parents=True)
    source.write_text(
        json.dumps(
            {
                "league_id": "strategy_league_v5_capital_10k",
                "status": "RUNNING",
                "initial_nav_usd": 10000.0,
                "rows": [
                    {
                        "strategy": "weekly_opportunity_ridge",
                        "return": -0.01,
                        "nav": 9900.0,
                    },
                    {
                        "strategy": "benchmark_spy",
                        "return": 0.002,
                        "nav": 10020.0,
                    },
                    {
                        "strategy": "capital_allocation_challenger",
                        "return": 0.006,
                        "nav": 10060.0,
                    },
                    {
                        "strategy": "breakout_protected_by_floor",
                        "return": -0.02,
                        "nav": 9800.0,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    output = tmp_path / "site" / "data" / "strategy_league.json"
    payload = publish_league_payload(data_dir, output)

    assert [row["strategy"] for row in payload["rows"]] == [
        "capital_allocation_challenger",
        "benchmark_spy",
        "weekly_opportunity_ridge",
        "breakout_protected_by_floor",
    ]
    assert [row["rank"] for row in payload["rows"]] == [1, 2, 3, 4]
    assert payload["summary"]["strategy_leader"] == "capital_allocation_challenger"
    assert payload["summary"]["challenger_rank"] == 1
    assert payload["summary"]["best_base_strategy"] == "weekly_opportunity_ridge"
    assert payload["summary"]["challenger_vs_spy"] == pytest.approx(0.004)
    assert payload["summary"]["challenger_vs_best_base"] == pytest.approx(0.016)
    assert payload["evidence_type"] == "prospective_shadow_paper"
    assert payload["automatic_promotion"] is False
    assert payload["live_execution_enabled"] is False
    assert output.exists()


def test_strategy_league_pages_surface_is_competitive_and_automatic() -> None:
    page = (ROOT / "site" / "strategies.html").read_text(encoding="utf-8")
    home = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "site" / "assets" / "league.js").read_text(encoding="utf-8")
    charts = (ROOT / "site" / "assets" / "charts.js").read_text(encoding="utf-8")
    styles = (ROOT / "site" / "assets" / "league.css").read_text(encoding="utf-8")

    assert 'id="strategy-league"' in page
    assert 'id="leagueSummary"' in page
    assert 'id="leagueCompetitionChart"' in page
    assert 'id="leagueTable"' in page
    assert "EOD → Strategy League → runtime state → Pages" in page
    assert 'href="assets/league.css"' in page
    assert 'href="strategies.html#strategy-league"' in home

    assert "capital_allocation_challenger: 'Capital Allocation Challenger'" in script
    assert "multiLineSvg" in script
    assert "challenger_vs_best_base" in script
    assert "costs_paid" in script
    assert "promotion_review_eligible" in script

    assert "export function multiLineSvg" in charts
    assert ".league-summary-grid" in styles
    assert ".league-series-0" in styles
    assert ".league-challenger-row" in styles
