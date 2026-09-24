from __future__ import annotations

import json
from pathlib import Path

import pytest

from league.publish_site import (
    publish_league_payload,
    publish_live_payload,
    publish_observation_payload,
)


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
                "league_id": "strategy_league_v7_clean_genesis_10k",
                "status": "RUNNING",
                "sessions": 8,
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
                        "strategy": "mean_reversion_floor_w1",
                        "return": 0.0,
                        "nav": 10000.0,
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
        "mean_reversion_floor_w1",
        "weekly_opportunity_ridge",
        "breakout_protected_by_floor",
    ]
    assert [row["rank"] for row in payload["rows"]] == [1, 2, 3, 4, 5]
    assert payload["summary"]["strategy_leader"] == "capital_allocation_challenger"
    assert payload["summary"]["leader_status"] == "PROVISIONAL"
    assert payload["summary"]["challenger_rank"] == 1
    assert payload["summary"]["best_base_strategy"] == "mean_reversion_floor_w1"
    assert payload["summary"]["challenger_vs_spy"] == pytest.approx(0.004)
    assert payload["summary"]["challenger_vs_best_base"] == pytest.approx(0.006)
    assert payload["evidence_type"] == "prospective_shadow_paper"
    assert payload["automatic_promotion"] is False
    assert payload["live_execution_enabled"] is False
    assert output.exists()

def test_league_with_one_session_and_all_tied_has_no_leader(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    source = data_dir / "metrics" / "strategy_league" / "leaderboard.json"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps({
        "status": "RUNNING", "sessions": 1,
        "rows": [{"strategy": name, "return": 0.0, "trades": 0} for name in
                 ("weekly_opportunity_ridge", "capital_allocation_challenger", "benchmark_spy")],
    }), encoding="utf-8")

    payload = publish_league_payload(data_dir, tmp_path / "site" / "data" / "strategy_league.json")

    assert payload["summary"]["leader_status"] == "INSUFFICIENT_EVIDENCE"
    assert payload["summary"]["strategy_leader"] is None
    assert payload["summary"]["overall_leader"] is None
    assert {row["rank"] for row in payload["rows"]} == {1}

def test_frozen_weekly_model_reports_weak_validation(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    weekly = data_dir / "metrics" / "strategy_league" / "models" / "weak.json"
    weekly.parent.mkdir(parents=True)
    weekly.write_text(json.dumps({"metrics": {
        "spearman_rank_correlation": 0.087,
        "top_quintile_return_lift": 0.0056,
        "top_quintile_net_return_lift": -0.0010,
    }}), encoding="utf-8")
    cfg = tmp_path / "league.json"
    cfg.write_text(json.dumps({"weekly_model_path": "data/metrics/strategy_league/models/weak.json"}), encoding="utf-8")
    result = publish_league_payload(data_dir, tmp_path / "site" / "data" / "league.json", cfg)
    assert result["weekly_model"]["status"] == "FROZEN"
    assert result["weekly_model"]["validation_warning"] is True

def test_publish_league_payload_rejects_stale_runtime_state(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    source = data_dir / "metrics" / "strategy_league" / "leaderboard.json"
    source.parent.mkdir(parents=True)
    source.write_text(
        json.dumps(
            {
                "league_id": "strategy_league_v4_old",
                "status": "RUNNING",
                "initial_nav_usd": 100000.0,
                "rows": [
                    {
                        "strategy": "weekly_opportunity_ridge",
                        "return": 0.50,
                        "nav": 150000.0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    league_config = tmp_path / "strategy_league.json"
    league_config.write_text(
        json.dumps(
            {
                "league_id": "strategy_league_v7_clean_genesis_10k",
                "initial_nav_usd": 10000.0,
                "members": [
                    {"id": "weekly_opportunity_ridge"},
                    {"id": "capital_allocation_challenger"},
                    {"id": "benchmark_spy"},
                ],
            }
        ),
        encoding="utf-8",
    )

    output = tmp_path / "site" / "data" / "strategy_league.json"
    payload = publish_league_payload(data_dir, output, league_config)

    assert payload["league_id"] == "strategy_league_v7_clean_genesis_10k"
    assert payload["status"] == "WAITING_FOR_GENESIS"
    assert payload["rows"] == []
    assert payload["initial_nav_usd"] == 10000.0
    assert payload["scheduled_members"] == [
        "weekly_opportunity_ridge",
        "capital_allocation_challenger",
        "benchmark_spy",
    ]
    assert "previous league strategy_league_v4_old" in payload["detail"]


def test_publish_observation_payload_rejects_previous_epoch(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    source = data_dir / "metrics" / "strategy_league" / "experiment_observation.json"
    source.parent.mkdir(parents=True)
    source.write_text(
        json.dumps(
            {
                "league_id": "strategy_league_v6_all_strategies_10k",
                "status": "RUNNING",
                "sessions": 9,
                "evidence": {"prediction_count_since_genesis": 999},
                "safety": {},
            }
        ),
        encoding="utf-8",
    )
    league_config = tmp_path / "strategy_league.json"
    league_config.write_text(
        json.dumps({"league_id": "strategy_league_v7_clean_genesis_10k"}),
        encoding="utf-8",
    )
    output = tmp_path / "site" / "data" / "experiment_observation.json"
    payload = publish_observation_payload(data_dir, output, league_config)

    assert payload["league_id"] == "strategy_league_v7_clean_genesis_10k"
    assert payload["status"] == "WAITING_FOR_GENESIS"
    assert payload["sessions"] == 0
    assert payload["evidence"]["prediction_count_since_genesis"] == 0

def test_strategy_league_config_tracks_every_base_strategy() -> None:
    config = json.loads(
        (ROOT / "config" / "strategy_league.json").read_text(encoding="utf-8")
    )
    member_ids = {str(member["id"]) for member in config["members"]}
    assert config["league_id"] == "strategy_league_v9_net_target_reversal_10k"
    assert "strategy_league_v9_net_target_reversal_10k" in config["weekly_model_path"]
    assert int(config["weekly_review_frequency_sessions"]) == 10
    assert float(config["execution"]["min_rebalance_weight_delta"]) == pytest.approx(0.02)
    assert float(config["execution"]["min_rebalance_notional_usd"]) == pytest.approx(100.0)
    assert float(config["initial_nav_usd"]) == 10000.0
    assert float(
        config["capital_allocation_challenger"]["source_weights"][
            "cross_horizon_asymmetry"
        ]
    ) == 0.0
    assert {
        "weekly_opportunity_ridge",
        "breakout_protected_by_floor",
        "mean_reversion_floor_w1",
        "cross_horizon_asymmetry",
        "capital_allocation_challenger",
        "benchmark_spy",
        "benchmark_equal_weight",
    } == member_ids

def test_strategy_league_pages_surface_is_competitive_and_automatic() -> None:
    page = (ROOT / "site" / "strategies.html").read_text(encoding="utf-8")
    home = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "site" / "assets" / "league.js").read_text(encoding="utf-8")
    charts = (ROOT / "site" / "assets" / "charts.js").read_text(encoding="utf-8")
    styles = (ROOT / "site" / "assets" / "league.css").read_text(encoding="utf-8")
    research = (ROOT / "site" / "assets" / "research.js").read_text(encoding="utf-8")

    assert 'id="strategy-live"' in page
    assert 'id="liveSummary"' in page
    assert 'id="liveCompetitionChart"' in page
    assert 'id="liveChartMetrics"' in page
    assert 'id="liveTable"' in page
    assert "actualización ~15 min" in page
    assert 'id="strategy-league"' in page
    assert 'id="leagueSummary"' in page
    assert 'id="leagueCompetitionChart"' in page
    assert 'id="leagueChartMetrics"' in page
    assert 'id="leagueWindow"' in page
    assert 'id="leagueTable"' in page
    assert "EOD → Strategy League → runtime state → Pages" in page
    assert 'href="assets/league.css"' in page
    assert 'href="strategies.html#strategy-league"' in home

    assert "capital_allocation_challenger: 'Capital Allocation Challenger'" in script
    assert "mean_reversion_floor_w1: 'Mean Reversion + Floor'" in script
    assert "cross_horizon_asymmetry: 'Cross-Horizon Asymmetry'" in script
    assert "SERIES_ORDER" in script
    assert "scheduled_members" in script
    assert "multiLineSvg" in script
    assert "challenger_vs_best_base" in script
    assert "costs_paid" in script
    assert "promotion_review_eligible" in script
    assert "data/strategy_live.json" in script
    assert "intraday_curve" in script
    assert "setInterval(renderLive, 60_000)" in script

    assert "export function multiLineSvg" in charts
    assert "export function filterPointsByWindow" in charts
    assert "'20d': 20" in charts
    assert "seriesIndex % 7" in charts
    assert "chart-threshold" in charts
    assert "width: 220" in charts
    assert "height: 72" in charts
    assert "rawEndLabels" in charts
    assert "shortLabel" in charts
    assert ".league-summary-grid" in styles
    assert ".league-series-0" in styles
    assert ".league-series-6" in styles
    assert ".league-challenger-row" in styles
    assert ".chart-kpi-strip" in styles
    assert ".chart-window-select" in styles
    assert ".chart-warmup" in styles
    assert "aspect-ratio: 220 / 72" in styles
    assert "min-height: 330px" not in styles
    assert "LIVE_MIN_POINTS = 3" in script
    assert "LEAGUE_MIN_SESSIONS = 5" in script
    assert "warmupChartState" in script
    assert "countLabel: 'Snapshots'" in script
    assert "sessionSuffix: ' ET'" in script
    assert "marketTime" in script
    assert "Hoy · ET" in page
    assert "Las horas se muestran en ET." in page
    assert "shortLabel: shortLabel(row.strategy)" in research
    assert 'id="oosWindow"' in page
    assert 'id="oosChartMetrics"' in page
    assert "F${fold.fold}" in research

def test_waiting_league_reports_genesis_when_frozen_weekly_exists(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    weekly = data_dir / "metrics" / "strategy_league" / "models" / "league-v7" / "weekly.json"
    weekly.parent.mkdir(parents=True)
    weekly.write_text(
        json.dumps({
            "model_name": "weekly_opportunity_ridge",
            "version": "weekly-v1",
            "metrics": {"spearman_rank_correlation": 0.22},
        }),
        encoding="utf-8",
    )
    league_config = tmp_path / "strategy_league.json"
    league_config.write_text(
        json.dumps({
            "league_id": "strategy_league_v7_clean_genesis_10k",
            "initial_nav_usd": 10000,
            "weekly_model_path": "data/metrics/strategy_league/models/league-v7/weekly.json",
            "members": [{"id": "weekly_opportunity_ridge"}],
        }),
        encoding="utf-8",
    )

    league_out = tmp_path / "site" / "data" / "strategy_league.json"
    observation_out = tmp_path / "site" / "data" / "experiment_observation.json"
    league_payload = publish_league_payload(data_dir, league_out, league_config)
    observation = publish_observation_payload(data_dir, observation_out, league_config)

    assert league_payload["status"] == "WAITING_FOR_GENESIS"
    assert league_payload["weekly_model"]["status"] == "FROZEN"
    assert league_payload["weekly_model"]["version"] == "weekly-v1"
    assert observation["status"] == "WAITING_FOR_GENESIS"
    weekly_observation = observation["models"]["weekly_opportunity_challenger"]
    assert weekly_observation["status"] == "FROZEN"
    assert weekly_observation["validation_metrics"]["spearman_rank_correlation"] == 0.22

def test_waiting_league_reports_missing_weekly_model_truthfully(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    league_config = tmp_path / "strategy_league.json"
    league_config.write_text(
        json.dumps({
            "league_id": "strategy_league_v7_clean_genesis_10k",
            "weekly_model_path": "data/metrics/strategy_league/models/missing.json",
        }),
        encoding="utf-8",
    )
    output = tmp_path / "site" / "data" / "strategy_league.json"

    payload = publish_league_payload(data_dir, output, league_config)

    assert payload["status"] == "WAITING_FOR_WEEKLY_MODEL"
    assert payload["weekly_model"]["status"] == "MISSING"


def test_publish_live_payload_is_non_promotional_and_strips_internal_cache(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    metrics = data_dir / "metrics" / "strategy_league"
    metrics.mkdir(parents=True)
    (metrics / "leaderboard.json").write_text(
        json.dumps(
            {
                "league_id": "strategy_league_v7_clean_genesis_10k",
                "status": "RUNNING",
                "last_session": "2026-09-23",
                "sessions": 9,
                "rows": [],
            }
        ),
        encoding="utf-8",
    )
    (metrics / "live_snapshot.json").write_text(
        json.dumps(
            {
                "league_id": "strategy_league_v7_clean_genesis_10k",
                "status": "LIVE",
                "last_eod_session": "2026-09-23",
                "market_session": "2026-09-24",
                "generated_at": "2026-09-24T15:00:00+00:00",
                "rows": [
                    {
                        "strategy": "capital_allocation_challenger",
                        "return": 0.02,
                        "nav": 10200,
                    },
                    {
                        "strategy": "benchmark_spy",
                        "return": 0.01,
                        "nav": 10100,
                    },
                ],
                "quote_cache": {"SPY": {"price": 700.0}},
                "automatic_promotion": True,
                "live_execution_enabled": True,
                "counts_as_prospective_evidence": True,
            }
        ),
        encoding="utf-8",
    )
    cfg = tmp_path / "strategy_league.json"
    cfg.write_text(
        json.dumps(
            {
                "league_id": "strategy_league_v7_clean_genesis_10k",
                "initial_nav_usd": 10000,
            }
        ),
        encoding="utf-8",
    )

    output = tmp_path / "site" / "data" / "strategy_live.json"
    payload = publish_live_payload(data_dir, output, cfg)

    assert payload["status"] == "LIVE"
    assert [row["rank"] for row in payload["rows"]] == [1, 2]
    assert "quote_cache" not in payload
    assert payload["counts_as_prospective_evidence"] is False
    assert payload["automatic_promotion"] is False
    assert payload["live_execution_enabled"] is False

def test_publish_live_payload_withholds_snapshot_from_older_eod_base(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    metrics = data_dir / "metrics" / "strategy_league"
    metrics.mkdir(parents=True)
    (metrics / "leaderboard.json").write_text(
        json.dumps(
            {
                "league_id": "strategy_league_v7_clean_genesis_10k",
                "last_session": "2026-09-24",
                "sessions": 10,
            }
        ),
        encoding="utf-8",
    )
    (metrics / "live_snapshot.json").write_text(
        json.dumps(
            {
                "league_id": "strategy_league_v7_clean_genesis_10k",
                "status": "LIVE",
                "last_eod_session": "2026-09-23",
                "rows": [
                    {
                        "strategy": "capital_allocation_challenger",
                        "return": 0.99,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    cfg = tmp_path / "strategy_league.json"
    cfg.write_text(
        json.dumps({"league_id": "strategy_league_v7_clean_genesis_10k"}),
        encoding="utf-8",
    )

    payload = publish_live_payload(
        data_dir,
        tmp_path / "site" / "data" / "strategy_live.json",
        cfg,
    )

    assert payload["status"] == "STALE_BASE"
    assert payload["rows"] == []
    assert payload["counts_as_prospective_evidence"] is False

def test_workflows_wire_intraday_strategy_channel_without_touching_daily_bars() -> None:
    live_workflow = (ROOT / ".github" / "workflows" / "strategy_live.yml").read_text(
        encoding="utf-8"
    )
    pages_workflow = (ROOT / ".github" / "workflows" / "pages.yml").read_text(
        encoding="utf-8"
    )
    eod_workflow = (ROOT / ".github" / "workflows" / "eod.yml").read_text(
        encoding="utf-8"
    )
    engine = (ROOT / "src" / "league" / "live_snapshot.py").read_text(
        encoding="utf-8"
    )

    assert "schedule:" in live_workflow
    assert "--interval 5m" in live_workflow
    assert "strategy-live-${{ github.run_id }}" in live_workflow
    assert "strategy_live" in pages_workflow
    assert "strategy-live-v1" in pages_workflow
    assert "--live-output site/data/strategy_live.json" in pages_workflow
    assert "league.live_snapshot export-base" in eod_workflow
    assert 'gh workflow run pages.yml --repo "${GITHUB_REPOSITORY}" --ref main' in eod_workflow
    assert "actions: write" in eod_workflow
    workflow_run_header = pages_workflow.split("push:", 1)[0]
    assert '"eod"' not in workflow_run_header
    assert "upsert_daily_bars" not in engine
