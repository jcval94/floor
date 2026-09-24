from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from replay.capital_tournament import _select_complete_session_run


def _row(symbol: str, session_day: date) -> dict:
    return {
        "symbol": symbol,
        "timestamp": datetime(
            session_day.year,
            session_day.month,
            session_day.day,
            16,
            0,
            tzinfo=timezone.utc,
        ).isoformat(),
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.5,
        "volume": 1000.0,
    }


def _daily_by_symbol(
    requested: list[date],
    *,
    missing: dict[str, set[date]] | None = None,
) -> dict[str, list[dict]]:
    missing = missing or {}
    return {
        symbol: [
            _row(symbol, session_day)
            for session_day in requested
            if session_day not in missing.get(symbol, set())
        ]
        for symbol in ("AAA", "BBB", "SPY")
    }


def test_session_selection_never_bridges_missing_market_day() -> None:
    requested = [
        date(2026, 9, 14),
        date(2026, 9, 15),
        date(2026, 9, 16),
        date(2026, 9, 17),
        date(2026, 9, 18),
        date(2026, 9, 21),
        date(2026, 9, 22),
        date(2026, 9, 23),
    ]
    daily = _daily_by_symbol(
        requested,
        missing={"AAA": {date(2026, 9, 22)}},
    )

    selected, audit = _select_complete_session_run(
        requested_sessions=requested,
        daily_by_symbol=daily,
        symbols=["AAA", "BBB"],
        min_sessions=2,
    )

    assert selected == requested[:6]
    assert audit["effective_end_session"] == "2026-09-21"
    assert audit["incomplete_sessions"] == ["2026-09-22"]
    assert audit["trimmed_complete_sessions"] == ["2026-09-23"]
    assert audit["missing_sessions_by_symbol"]["AAA"] == ["2026-09-22"]
    assert audit["selection_rule"] == (
        "longest_contiguous_common_complete_daily_bar_run"
    )


def test_session_selection_prefers_most_recent_run_on_equal_length() -> None:
    requested = [
        date(2026, 9, 14),
        date(2026, 9, 15),
        date(2026, 9, 16),
        date(2026, 9, 17),
        date(2026, 9, 18),
    ]
    daily = _daily_by_symbol(
        requested,
        missing={"SPY": {date(2026, 9, 16)}},
    )

    selected, audit = _select_complete_session_run(
        requested_sessions=requested,
        daily_by_symbol=daily,
        symbols=["AAA", "BBB"],
        min_sessions=2,
    )

    assert selected == requested[3:]
    assert audit["effective_start_session"] == "2026-09-17"
    assert audit["effective_end_session"] == "2026-09-18"


def test_session_selection_requires_enough_contiguous_data() -> None:
    requested = [
        date(2026, 9, 14),
        date(2026, 9, 15),
        date(2026, 9, 16),
        date(2026, 9, 17),
    ]
    daily = _daily_by_symbol(
        requested,
        missing={"BBB": {date(2026, 9, 15), date(2026, 9, 17)}},
    )

    with pytest.raises(RuntimeError, match="insufficient contiguous complete sessions"):
        _select_complete_session_run(
            requested_sessions=requested,
            daily_by_symbol=daily,
            symbols=["AAA", "BBB"],
            min_sessions=2,
        )
