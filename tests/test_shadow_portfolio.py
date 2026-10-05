from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from league import shadow_portfolio
from league.shadow_portfolio import build_shadow_snapshot

ET = ZoneInfo("America/New_York")


def _base() -> dict:
    return {
        "schema_version": 1,
        "league_id": "strategy_league_v11_intraday_informed_10k",
        "status": "READY",
        "last_eod_session": "2026-10-01",
        "official_state_hash": "state-hash-2026-10-01",
        "sessions": 5,
        "initial_nav_usd": 10000.0,
        "members": {
            "capital_allocation_challenger": {
                "id": "capital_allocation_challenger",
                "member_type": "strategy",
                "cash": 10000.0,
                "positions": {},
                "pending_targets": {
                    "AAA": {
                        "weight": 0.5,
                        "stop_price": 95.0,
                        "take_profit_price": 110.0,
                    }
                },
                "trade_count": 0,
                "costs_paid": 0.0,
                "gross_traded_notional": 0.0,
                "eod_nav": 10000.0,
            }
        },
    }


def _cfg() -> dict:
    return {
        "execution": {
            "commission_bps": 26,
            "slippage_bps": 3,
            "sell_fee_bps": 3,
            "min_rebalance_weight_delta": 0.02,
            "min_rebalance_notional_usd": 100,
        }
    }


def _bar(ts: str, open_: float, high: float, low: float, close: float) -> dict:
    return {"ts": ts, "open": open_, "high": high, "low": low, "close": close}


def test_open_shadow_fill_uses_league_execution_costs_and_is_idempotent() -> None:
    now = datetime(2026, 10, 2, 10, 5, tzinfo=ET)
    bars = {
        "AAA": [
            _bar("2026-10-02T13:30:00+00:00", 100.0, 102.0, 99.0, 101.0),
        ]
    }

    first = build_shadow_snapshot(_base(), {}, _cfg(), now=now, session_bars=bars)
    fills = first["shadow_open_fills"]
    assert first["open_fills_applied"] is True
    assert first["shadow_evidence_complete"] is True
    assert len(fills) == 1
    assert fills[0]["qty"] == 50
    assert fills[0]["raw_price"] == pytest.approx(100.0)
    assert fills[0]["fill_price"] == pytest.approx(100.03)
    assert fills[0]["costs"] == pytest.approx(14.5039, rel=1e-5)
    assert first["live_execution_enabled"] is False

    second = build_shadow_snapshot(_base(), first, _cfg(), now=now, session_bars=bars)
    assert len(second["shadow_open_fills"]) == 1
    assert second["shadow_open_fills"][0]["idempotency_key"] == fills[0]["idempotency_key"]
    assert second["rows"][0]["trades"] == 1


def test_intraday_take_exit_uses_first_point_in_time_touch_and_sell_costs() -> None:
    now = datetime(2026, 10, 2, 11, 5, tzinfo=ET)
    bars = {
        "AAA": [
            _bar("2026-10-02T13:30:00+00:00", 100.0, 102.0, 99.0, 101.0),
            _bar("2026-10-02T14:30:00+00:00", 108.0, 111.0, 107.0, 110.0),
        ]
    }

    payload = build_shadow_snapshot(_base(), {}, _cfg(), now=now, session_bars=bars)
    assert len(payload["shadow_open_fills"]) == 1
    assert len(payload["shadow_exits"]) == 1
    exit_trade = payload["shadow_exits"][0]
    assert exit_trade["side"] == "SELL"
    assert exit_trade["reason"] == "take_profit_touched"
    assert exit_trade["raw_price"] == pytest.approx(110.0)
    assert payload["rows"][0]["positions"] == []
    assert payload["rows"][0]["costs"] > payload["shadow_open_fills"][0]["costs"]


def test_missing_open_data_never_invents_fill() -> None:
    payload = build_shadow_snapshot(
        _base(),
        {},
        _cfg(),
        now=datetime(2026, 10, 2, 10, 5, tzinfo=ET),
        session_bars={},
        failed_symbols=["AAA"],
    )

    assert payload["status"] == "DEGRADED"
    assert payload["open_fill_status"] == "MISSING_OPEN_DATA"
    assert payload["open_fills_applied"] is False
    assert payload["shadow_open_fills"] == []


def test_repeated_heartbeats_append_observations_without_reopening_positions() -> None:
    first_bars = {
        "AAA": [_bar("2026-10-02T13:30:00+00:00", 100.0, 102.0, 99.0, 101.0)]
    }
    first = build_shadow_snapshot(
        _base(),
        {},
        _cfg(),
        now=datetime(2026, 10, 2, 10, 5, tzinfo=ET),
        session_bars=first_bars,
    )
    second_bars = {
        "AAA": [
            *first_bars["AAA"],
            _bar("2026-10-02T14:00:00+00:00", 101.0, 104.0, 100.0, 103.0),
        ]
    }
    second = build_shadow_snapshot(
        _base(),
        first,
        _cfg(),
        now=datetime(2026, 10, 2, 10, 35, tzinfo=ET),
        session_bars=second_bars,
    )

    assert len(second["shadow_open_fills"]) == 1
    assert len(second["observations"]) == 2
    assert second["rows"][0]["nav"] > first["rows"][0]["nav"]


def test_legacy_live_base_without_pending_targets_marks_shadow_evidence_incomplete() -> None:
    base = _base()
    del base["members"]["capital_allocation_challenger"]["pending_targets"]
    payload = build_shadow_snapshot(
        base,
        {},
        _cfg(),
        now=datetime(2026, 10, 2, 10, 5, tzinfo=ET),
        session_bars={},
    )

    assert payload["shadow_evidence_complete"] is False
    assert payload["pending_targets_contract_present"] is False
    assert payload["shadow_evidence_incomplete_reason"] == (
        "legacy_live_base_missing_pending_targets"
    )
    assert payload["shadow_open_fills"] == []


def test_previous_shadow_is_rebuilt_when_authoritative_base_hash_changes() -> None:
    now = datetime(2026, 10, 2, 10, 35, tzinfo=ET)
    bars = {
        "AAA": [
            _bar("2026-10-02T13:30:00+00:00", 100.0, 102.0, 99.0, 101.0),
            _bar("2026-10-02T14:00:00+00:00", 101.0, 103.0, 100.0, 102.0),
        ]
    }
    first = build_shadow_snapshot(
        _base(),
        {},
        _cfg(),
        now=datetime(2026, 10, 2, 10, 5, tzinfo=ET),
        session_bars=bars,
    )
    assert first["source_base_state_hash"] == "state-hash-2026-10-01"

    corrected = _base()
    corrected["official_state_hash"] = "corrected-authoritative-state"
    corrected["members"]["capital_allocation_challenger"]["pending_targets"]["AAA"]["weight"] = 0.25

    rebuilt = build_shadow_snapshot(
        corrected,
        first,
        _cfg(),
        now=now,
        session_bars=bars,
    )

    assert rebuilt["source_base_state_hash"] == "corrected-authoritative-state"
    assert len(rebuilt["observations"]) == 1
    assert rebuilt["shadow_open_fills"][0]["qty"] == 25


def test_eod_replay_can_use_hash_bound_authoritative_state_older_than_t1(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = _base()
    base["last_eod_session"] = "2026-09-30"
    base_path = tmp_path / "base.json"
    previous_path = tmp_path / "previous.json"
    config_path = tmp_path / "league.json"
    output_path = tmp_path / "shadow.json"
    base_path.write_text(json.dumps(base), encoding="utf-8")
    previous_path.write_text("{}", encoding="utf-8")
    config_path.write_text(
        json.dumps({"league_id": base["league_id"], **_cfg()}),
        encoding="utf-8",
    )
    bars = {
        "AAA": [_bar("2026-10-02T13:30:00+00:00", 100.0, 102.0, 99.0, 101.0)]
    }
    monkeypatch.setattr(
        shadow_portfolio,
        "fetch_session_bars",
        lambda *args, **kwargs: (bars, []),
    )
    now = datetime(2026, 10, 2, 10, 5, tzinfo=ET)

    with pytest.raises(RuntimeError, match="not T-1 authoritative state"):
        shadow_portfolio.run(
            base_path=base_path,
            previous_path=previous_path,
            config_path=config_path,
            output_path=output_path,
            now=now,
        )

    payload = shadow_portfolio.run(
        base_path=base_path,
        previous_path=previous_path,
        config_path=config_path,
        output_path=output_path,
        now=now,
        authoritative_eod_replay=True,
    )

    assert payload["source_base_state_hash"] == base["official_state_hash"]
    assert payload["open_fills_applied"] is True
