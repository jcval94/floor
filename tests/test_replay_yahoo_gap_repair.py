from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from replay.capital_tournament import _select_complete_session_run
from replay.yahoo_source import repair_replay_daily_market_data


ET = ZoneInfo("America/New_York")


def _row(symbol: str, session_day: date, close: float = 100.0) -> dict:
    return {
        "symbol": symbol,
        "timestamp": datetime(
            session_day.year,
            session_day.month,
            session_day.day,
            9,
            30,
            tzinfo=ET,
        ).isoformat(),
        "open": close - 0.5,
        "high": close + 1.0,
        "low": close - 1.0,
        "close": close,
        "volume": 1000.0,
    }


def test_gap_repair_recovers_transient_missing_sessions_before_trimming(monkeypatch) -> None:
    sessions = [
        date(2026, 9, 21),
        date(2026, 9, 22),
        date(2026, 9, 23),
        date(2026, 9, 24),
    ]
    base = [
        _row(symbol, session_day, 100 + idx)
        for symbol in ("AAA", "BBB", "SPY")
        for idx, session_day in enumerate(sessions)
        if not (
            session_day == date(2026, 9, 22)
            and symbol in {"AAA", "BBB"}
        )
    ]

    def fake_fetch(symbol: str, *, range_: str, interval: str, attempts: int = 3):
        assert interval == "1d"
        assert range_ == "3mo"
        assert attempts == 4
        if symbol in {"AAA", "BBB"}:
            return [
                _row(symbol, date(2026, 9, 22), 222.0),
                _row(symbol, date(2026, 9, 23), 999.0),
            ]
        raise AssertionError(f"unexpected repair symbol {symbol}")

    monkeypatch.setattr("replay.yahoo_source.fetch_rows_with_retries", fake_fetch)

    repaired, audit = repair_replay_daily_market_data(
        base,
        symbols=["AAA", "BBB"],
        required_sessions=sessions,
        sleep_seconds=0.0,
    )

    assert audit["attempted"] is True
    assert audit["added_rows"] == 2
    assert audit["unresolved_missing_sessions_by_symbol"] == {}
    assert audit["recovered_sessions_by_symbol"] == {
        "AAA": ["2026-09-22"],
        "BBB": ["2026-09-22"],
    }
    assert audit["repair_attempts"] == [
        {
            "range": "3mo",
            "symbols_requested": 2,
            "added_rows": 2,
            "remaining_missing_symbols": 0,
            "failures": [],
        }
    ]

    # The repair may add missing bars only; it must not overwrite 23-Sep.
    aaa_23 = [
        row
        for row in repaired
        if row["symbol"] == "AAA"
        and str(row["timestamp"]).startswith("2026-09-23")
    ]
    assert len(aaa_23) == 1
    assert aaa_23[0]["close"] != 999.0

    by_symbol = {
        symbol: [row for row in repaired if row["symbol"] == symbol]
        for symbol in ("AAA", "BBB", "SPY")
    }
    selected, selection = _select_complete_session_run(
        requested_sessions=sessions,
        daily_by_symbol=by_symbol,
        symbols=["AAA", "BBB"],
        min_sessions=2,
    )
    assert selected == sessions
    assert selection["incomplete_sessions"] == []
    assert selection["trimmed_complete_sessions"] == []
    assert selection["effective_end_session"] == "2026-09-24"


def test_gap_repair_preserves_conservative_trim_when_provider_still_has_hole(monkeypatch) -> None:
    sessions = [
        date(2026, 9, 21),
        date(2026, 9, 22),
        date(2026, 9, 23),
    ]
    base = [
        _row(symbol, session_day)
        for symbol in ("AAA", "BBB", "SPY")
        for session_day in sessions
        if not (symbol == "AAA" and session_day == date(2026, 9, 22))
    ]

    def fake_fetch(symbol: str, *, range_: str, interval: str, attempts: int = 3):
        assert symbol == "AAA"
        return [_row("AAA", date(2026, 9, 21))]

    monkeypatch.setattr("replay.yahoo_source.fetch_rows_with_retries", fake_fetch)

    repaired, audit = repair_replay_daily_market_data(
        base,
        symbols=["AAA", "BBB"],
        required_sessions=sessions,
        sleep_seconds=0.0,
    )

    assert audit["attempted"] is True
    assert audit["added_rows"] == 0
    assert audit["unresolved_missing_sessions_by_symbol"] == {
        "AAA": ["2026-09-22"]
    }
    assert [attempt["range"] for attempt in audit["repair_attempts"]] == [
        "3mo",
        "6mo",
        "2y",
    ]

    by_symbol = {
        symbol: [row for row in repaired if row["symbol"] == symbol]
        for symbol in ("AAA", "BBB", "SPY")
    }
    selected, selection = _select_complete_session_run(
        requested_sessions=sessions,
        daily_by_symbol=by_symbol,
        symbols=["AAA", "BBB"],
        min_sessions=1,
    )
    assert selected == [date(2026, 9, 23)]
    assert selection["incomplete_sessions"] == ["2026-09-22"]
