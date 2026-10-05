from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from league import live_snapshot
from league.engine import initialize_league
from league.live_snapshot import (
    build_live_snapshot,
    export_live_base,
    fetch_checkpoint_quotes,
)


def _base() -> dict:
    return {
        "schema_version": 1,
        "league_id": "strategy_league_v7_clean_genesis_10k",
        "status": "READY",
        "last_eod_session": "2026-09-23",
        "sessions": 9,
        "initial_nav_usd": 10000.0,
        "members": {
            "capital_allocation_challenger": {
                "member_type": "strategy",
                "cash": 5000.0,
                "eod_nav": 10000.0,
                "positions": {
                    "AAA": {
                        "qty": 50,
                        "cost_basis": 95.0,
                        "last_price": 100.0,
                    }
                },
                "official_sharpe": 1.2,
                "official_max_drawdown": -0.03,
                "trades": 5,
                "costs_paid": 12.0,
            },
            "weekly_opportunity_ridge": {
                "member_type": "strategy",
                "cash": 10000.0,
                "eod_nav": 10000.0,
                "positions": {},
                "official_sharpe": 0.5,
                "official_max_drawdown": -0.01,
                "trades": 0,
                "costs_paid": 0.0,
            },
            "benchmark_spy": {
                "member_type": "benchmark",
                "cash": 0.0,
                "eod_nav": 10000.0,
                "positions": {
                    "SPY": {
                        "qty": 100,
                        "cost_basis": 100.0,
                        "last_price": 100.0,
                    }
                },
                "trades": 1,
                "costs_paid": 1.0,
            },
        },
    }


NOW = datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc)


def test_live_snapshot_marks_official_positions_without_mutating_eod_metrics() -> None:
    payload = build_live_snapshot(
        _base(),
        {
            "AAA": {
                "price": 110.0,
                "as_of": "2026-09-24T14:59:00+00:00",
            },
            "SPY": {
                "price": 102.0,
                "as_of": "2026-09-24T14:59:00+00:00",
            },
        },
        now=NOW,
    )

    rows = {row["strategy"]: row for row in payload["rows"]}
    challenger = rows["capital_allocation_challenger"]

    assert payload["status"] == "LIVE"
    assert payload["market_session"] == "2026-09-24"
    assert payload["quote_source"]["fresh_coverage"] == pytest.approx(1.0)
    assert payload["counts_as_prospective_evidence"] is False
    assert payload["automatic_promotion"] is False
    assert payload["live_execution_enabled"] is False

    assert challenger["nav"] == pytest.approx(10500.0)
    assert challenger["eod_nav"] == pytest.approx(10000.0)
    assert challenger["return"] == pytest.approx(0.05)
    assert challenger["change_since_eod"] == pytest.approx(0.05)
    assert challenger["vs_spy"] == pytest.approx(0.03)
    assert challenger["gross_exposure_pct"] == pytest.approx(5500 / 10500)
    assert challenger["quote_coverage"] == pytest.approx(1.0)
    assert challenger["rank"] == 1
    assert challenger["official_sharpe"] == pytest.approx(1.2)


def test_live_snapshot_degrades_instead_of_pretending_eod_price_is_live() -> None:
    payload = build_live_snapshot(
        _base(),
        {
            "SPY": {
                "price": 102.0,
                "as_of": "2026-09-24T14:59:00+00:00",
            }
        },
        now=NOW,
        failed_symbols=["AAA"],
    )

    rows = {row["strategy"]: row for row in payload["rows"]}
    challenger = rows["capital_allocation_challenger"]

    assert payload["status"] == "DEGRADED"
    assert payload["quote_source"]["fresh_coverage"] == pytest.approx(0.5)
    assert payload["quote_source"]["failed_symbols"] == ["AAA"]
    assert challenger["nav"] == pytest.approx(10000.0)
    assert challenger["fresh_quotes"] == 0
    assert challenger["fallback_quotes"] == 1
    assert challenger["quote_coverage"] == pytest.approx(0.0)


def test_live_snapshot_can_use_same_session_cache_but_keeps_coverage_honest() -> None:
    previous = {
        "market_session": "2026-09-24",
        "quote_cache": {
            "AAA": {
                "price": 108.0,
                "as_of": "2026-09-24T14:45:00+00:00",
            }
        },
        "rows": [
            {
                "strategy": "capital_allocation_challenger",
                "intraday_curve": [{"session": "10:45", "nav": 10400.0}],
            }
        ],
    }
    payload = build_live_snapshot(
        _base(),
        {
            "SPY": {
                "price": 102.0,
                "as_of": "2026-09-24T14:59:00+00:00",
            }
        },
        previous_snapshot=previous,
        now=NOW,
    )

    challenger = next(
        row
        for row in payload["rows"]
        if row["strategy"] == "capital_allocation_challenger"
    )
    assert payload["status"] == "DEGRADED"
    assert challenger["nav"] == pytest.approx(10400.0)
    assert challenger["cached_quotes"] == 1
    assert challenger["fallback_quotes"] == 0
    assert challenger["quote_coverage"] == pytest.approx(0.0)
    assert challenger["intraday_curve"] == [
        {"session": "10:45", "nav": 10400.0},
        {"session": "11:00", "nav": 10400.0},
    ]


def test_intraday_curve_resets_on_new_market_session() -> None:
    previous = {
        "market_session": "2026-09-23",
        "rows": [
            {
                "strategy": "capital_allocation_challenger",
                "intraday_curve": [{"session": "15:50", "nav": 10100.0}],
            }
        ],
    }
    payload = build_live_snapshot(
        _base(),
        {
            "AAA": {
                "price": 110.0,
                "as_of": "2026-09-24T14:59:00+00:00",
            },
            "SPY": {
                "price": 102.0,
                "as_of": "2026-09-24T14:59:00+00:00",
            },
        },
        previous_snapshot=previous,
        now=NOW,
    )
    challenger = next(
        row
        for row in payload["rows"]
        if row["strategy"] == "capital_allocation_challenger"
    )
    assert challenger["intraday_curve"] == [{"session": "11:00", "nav": 10500.0}]


def test_checkpoint_quotes_exclude_bars_not_completed_by_accepted_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timestamps = [
        int(datetime(2026, 10, 1, 15, 15, tzinfo=timezone.utc).timestamp()),
        int(datetime(2026, 10, 1, 15, 20, tzinfo=timezone.utc).timestamp()),
        int(datetime(2026, 10, 1, 15, 25, tzinfo=timezone.utc).timestamp()),
        int(datetime(2026, 10, 1, 15, 30, tzinfo=timezone.utc).timestamp()),
    ]
    payload = {
        "chart": {
            "result": [
                {
                    "meta": {"previousClose": 100.0},
                    "timestamp": timestamps,
                    "indicators": {
                        "quote": [
                            {
                                "open": [100.0, 100.5, 101.0, 999.0],
                                "high": [100.6, 101.1, 102.5, 999.0],
                                "low": [99.9, 100.4, 100.9, 999.0],
                                "close": [100.5, 101.0, 102.0, 999.0],
                                "volume": [1000, 1100, 1200, 1],
                            }
                        ]
                    },
                }
            ]
        }
    }
    monkeypatch.setattr(
        live_snapshot,
        "fetch_yahoo_chart",
        lambda *_args, **_kwargs: payload,
    )

    quotes, failed = fetch_checkpoint_quotes(
        ["AAA"],
        checkpoint_at=datetime(
            2026,
            10,
            1,
            11,
            30,
            tzinfo=live_snapshot.ET,
        ),
        range_="1d",
        interval="5m",
        max_workers=1,
    )

    assert failed == []
    assert quotes["AAA"]["price"] == pytest.approx(102.0)
    assert quotes["AAA"]["as_of"].startswith("2026-10-01T15:25:00")
    assert quotes["AAA"]["session_return"] == pytest.approx(0.02)
    assert quotes["AAA"]["price"] != pytest.approx(999.0)



def test_exported_compact_base_is_provenance_stamped_derived_cache(
    tmp_path: Path,
) -> None:
    league_id = "test_live_base_authority"
    cfg = {
        "league_id": league_id,
        "initial_nav_usd": 10000.0,
        "members": [
            {"id": "strategy_a", "type": "strategy"},
            {"id": "benchmark_spy", "type": "benchmark"},
        ],
    }
    config_path = tmp_path / "strategy_league.json"
    config_path.write_text(json.dumps(cfg), encoding="utf-8")
    state_dir = (
        tmp_path
        / "data"
        / "metrics"
        / "strategy_league"
        / "runs"
        / league_id
    )
    initialize_league(
        state_dir,
        cfg,
        "2026-10-02",
        {
            "league_config_sha256": "league",
            "strategies_config_sha256": "strategies",
            "weekly_model_sha256": "weekly",
        },
        {
            "strategy_a": {},
            "benchmark_spy": {"SPY": {"weight": 1.0}},
        },
    )
    output = tmp_path / "live_base.json"

    payload = export_live_base(
        tmp_path / "data",
        config_path,
        output,
    )

    assert payload["schema_version"] == 2
    assert payload["source_authority"] == "runtime_state_hash_chain"
    assert payload["source_last_session"] == "2026-10-02"
    assert payload["source_session_count"] == 1
    assert len(payload["source_history_sha256"]) == 64
    assert len(payload["source_audit_hash"]) == 64
