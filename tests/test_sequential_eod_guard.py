from __future__ import annotations

import pytest

from league.run_eod import validate_sequential_eod_session


def test_official_eod_only_advances_one_nyse_session() -> None:
    # Friday -> Monday, with no exchange holiday in between.
    validate_sequential_eod_session("2026-10-16", "2026-10-19")
    validate_sequential_eod_session("2026-10-14", "2026-10-15")
    validate_sequential_eod_session("2026-10-15", "2026-10-16")
    validate_sequential_eod_session("2026-10-14", "2026-10-14")


@pytest.mark.parametrize(
    ("previous", "target"),
    [
        ("2026-10-14", "2026-10-16"),  # Missed Oct 15.
        ("2026-10-14", "2026-10-19"),  # Missed Oct 15 and 16.
        ("2026-10-15", "2026-10-19"),  # Missed Oct 16.
    ],
)
def test_official_eod_refuses_to_jump_over_uncommitted_sessions(
    previous: str, target: str,
) -> None:
    with pytest.raises(RuntimeError, match="historical EOD gap"):
        validate_sequential_eod_session(previous, target)


@pytest.mark.parametrize(
    ("previous", "target"),
    [
        ("2026-10-10", "2026-10-12"),  # Saturday is never last official session.
        ("2026-10-14", "2026-10-17"),  # Saturday is never the new CLOSE.
        ("garbage", "2026-10-16"),
    ],
)
def test_official_eod_rejects_invalid_market_dates(
    previous: str, target: str,
) -> None:
    with pytest.raises(RuntimeError):
        validate_sequential_eod_session(previous, target)
