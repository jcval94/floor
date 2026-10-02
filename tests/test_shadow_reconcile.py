from __future__ import annotations

import json
from pathlib import Path

import pytest

from league.shadow_reconcile import reconcile


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def _trade(fill: float = 100.03, costs: float = 14.5039) -> dict:
    return {
        "member": "capital_allocation_challenger",
        "symbol": "AAA",
        "side": "BUY",
        "qty": 50,
        "fill_price": fill,
        "raw_price": 100.0,
        "costs": costs,
        "reason": "signal_t_to_open_t_plus_1",
    }


def test_shadow_eod_matching_trade_reconciles(tmp_path: Path) -> None:
    shadow = tmp_path / "shadow.json"
    history = tmp_path / "history.jsonl"
    output = tmp_path / "reconciliation.json"
    _write(
        shadow,
        {
            "market_session": "2026-10-02",
            "shadow_open_fills": [_trade()],
            "shadow_exits": [],
        },
    )
    _write(history, {"session": "2026-10-02", "event": "EOD", "trades": [_trade()]})

    payload = reconcile(shadow_path=shadow, history_path=history, output_path=output)

    assert payload["status"] == "RECONCILED"
    assert payload["matched_trade_count"] == 1
    assert payload["divergences"] == []


def test_shadow_eod_material_fill_difference_fails_closed(tmp_path: Path) -> None:
    shadow = tmp_path / "shadow.json"
    history = tmp_path / "history.jsonl"
    output = tmp_path / "reconciliation.json"
    _write(
        shadow,
        {
            "market_session": "2026-10-02",
            "shadow_open_fills": [_trade(fill=100.03)],
            "shadow_exits": [],
        },
    )
    _write(
        history,
        {"session": "2026-10-02", "event": "EOD", "trades": [_trade(fill=101.0)]},
    )

    with pytest.raises(RuntimeError, match="Material shadow/EOD divergence"):
        reconcile(shadow_path=shadow, history_path=history, output_path=output)

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "DIVERGED"
    assert payload["divergences"][0]["kind"] == "trade_mismatch"


def test_legacy_shadow_without_pending_targets_is_explicitly_not_comparable(
    tmp_path: Path,
) -> None:
    shadow = tmp_path / "shadow.json"
    history = tmp_path / "history.jsonl"
    output = tmp_path / "reconciliation.json"
    _write(
        shadow,
        {
            "market_session": "2026-10-02",
            "shadow_evidence_complete": False,
            "shadow_evidence_incomplete_reason": "legacy_live_base_missing_pending_targets",
            "shadow_open_fills": [],
            "shadow_exits": [],
        },
    )
    _write(
        history,
        {"session": "2026-10-02", "event": "EOD", "trades": [_trade()]},
    )

    payload = reconcile(
        shadow_path=shadow,
        history_path=history,
        output_path=output,
    )

    assert payload["status"] == "INCOMPLETE_EVIDENCE"
    assert payload["reconciliation_skipped"] is True
    assert payload["divergences"] == []
    assert payload["official_trade_count"] == 1
    assert payload["explained_differences"][0]["kind"] == (
        "legacy_live_base_missing_pending_targets"
    )
