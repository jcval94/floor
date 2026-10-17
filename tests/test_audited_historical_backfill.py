from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from audited_eod_backfill import validate_request  # noqa: E402


ET = ZoneInfo("America/New_York")


def _request() -> dict:
    return {
        "mode": "historical_eod_backfill",
        "authorization": "CONFIRM_TWO_SESSION_AUDITED_BACKFILL",
        "source_last_session": "2026-10-14",
        "target_sessions": ["2026-10-15", "2026-10-16"],
    }


def test_explicit_historical_close_pair_allowed_during_weekend() -> None:
    assert validate_request(
        _request(), now=datetime(2026, 10, 17, 11, 0, tzinfo=ET)
    ) == ["2026-10-15", "2026-10-16"]


@pytest.mark.parametrize(
    "when", [
        datetime(2026, 10, 16, 16, 5, tzinfo=ET),
        datetime(2026, 10, 19, 9, 31, tzinfo=ET),
    ],
)
def test_historical_backfill_refuses_before_close_and_after_next_open(when) -> None:
    with pytest.raises(RuntimeError, match="window closed"):
        validate_request(_request(), now=when)


@pytest.mark.parametrize(
    "mutation", [
        {"authorization": "wrong"},
        {"source_last_session": "2026-10-13"},
        {"target_sessions": ["2026-10-15", "2026-10-19"]},
        {"target_sessions": ["2026-10-16", "2026-10-15"]},
        {"target_sessions": ["2026-10-15"]},
        {"target_sessions": ["2026-10-17", "2026-10-19"]},
    ],
)
def test_historical_backfill_refuses_invalid_provenance(mutation) -> None:
    with pytest.raises(RuntimeError):
        validate_request(
            {**_request(), **mutation},
            now=datetime(2026, 10, 17, 11, tzinfo=ET),
        )


def test_historical_workflow_is_single_event_and_promotes_after_both_reconcile() -> None:
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/historical_eod_backfill.yml").read_text()
    script = (root / "scripts/audited_eod_backfill.py").read_text()
    assert "historical_eod_backfill_request.json" in workflow
    assert "schedule:" not in workflow
    assert "Publish reconciled rolling runtime state atomically" in workflow
    assert workflow.index("Reconstruct and audit both historical EOD sessions locally") < (
        workflow.index("Publish reconciled rolling runtime state atomically")
    )
    assert workflow.index("Publish reconciled rolling runtime state atomically") < (
        workflow.index("Dispatch Pages from durable recovered CLOSE")
    )
    assert "validate_market_data_freshness" in script
    assert "--required-market-session" in script
    assert "shadow_eod_" in script
    assert 'recon.get("status") != "RECONCILED"' in script
    assert "historical EOD sessions are not adjacent" in script
