from __future__ import annotations

from datetime import datetime, timezone

import pytest

from league.live_snapshot import build_live_snapshot


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
