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


def test_daily_ohlc_only_stop_exit_is_explained_not_material(tmp_path: Path) -> None:
    shadow = tmp_path / "shadow.json"
    history = tmp_path / "history.jsonl"
    output = tmp_path / "reconciliation.json"
    _write(
        shadow,
        {
            "market_session": "2026-10-02",
            "shadow_open_fills": [],
            "shadow_exits": [],
        },
    )
    official_exit = {
        **_trade(),
        "side": "SELL",
        "reason": "stop_touched_conservative_first",
    }
    _write(
        history,
        {"session": "2026-10-02", "event": "EOD", "trades": [official_exit]},
    )

    payload = reconcile(shadow_path=shadow, history_path=history, output_path=output)

    assert payload["status"] == "RECONCILED"
    assert payload["divergences"] == []
    assert payload["explained_difference_counts"] == {
        "daily_ohlc_only_exit_source_granularity": 1
    }


def test_intraday_only_take_exit_is_explained_not_material(tmp_path: Path) -> None:
    shadow = tmp_path / "shadow.json"
    history = tmp_path / "history.jsonl"
    output = tmp_path / "reconciliation.json"
    shadow_exit = {
        **_trade(),
        "side": "SELL",
        "reason": "take_profit_touched",
        "touched_at": "2026-10-02T15:00:00+00:00",
    }
    _write(
        shadow,
        {
            "market_session": "2026-10-02",
            "shadow_open_fills": [],
            "shadow_exits": [shadow_exit],
        },
    )
    _write(history, {"session": "2026-10-02", "event": "EOD", "trades": []})

    payload = reconcile(shadow_path=shadow, history_path=history, output_path=output)

    assert payload["status"] == "RECONCILED"
    assert payload["divergences"] == []
    assert payload["explained_difference_counts"] == {
        "intraday_only_exit_source_granularity": 1
    }


def test_unmatched_official_open_trade_remains_material(tmp_path: Path) -> None:
    shadow = tmp_path / "shadow.json"
    history = tmp_path / "history.jsonl"
    output = tmp_path / "reconciliation.json"
    _write(
        shadow,
        {
            "market_session": "2026-10-02",
            "shadow_open_fills": [],
            "shadow_exits": [],
        },
    )
    _write(history, {"session": "2026-10-02", "event": "EOD", "trades": [_trade()]})

    with pytest.raises(RuntimeError, match="unexpected_official_only_trade"):
        reconcile(shadow_path=shadow, history_path=history, output_path=output)

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["divergence_counts"] == {"unexpected_official_only_trade": 1}


def test_duplicate_trade_keys_are_matched_as_a_multiset(tmp_path: Path) -> None:
    shadow = tmp_path / "shadow.json"
    history = tmp_path / "history.jsonl"
    output = tmp_path / "reconciliation.json"
    trades = [_trade(), _trade()]
    _write(
        shadow,
        {
            "market_session": "2026-10-02",
            "shadow_open_fills": trades,
            "shadow_exits": [],
        },
    )
    _write(
        history,
        {"session": "2026-10-02", "event": "EOD", "trades": trades},
    )

    payload = reconcile(shadow_path=shadow, history_path=history, output_path=output)

    assert payload["status"] == "RECONCILED"
    assert payload["matched_trade_count"] == 2


def test_recorded_wfc_yahoo_open_variance_is_explained_but_audited(
    tmp_path: Path,
) -> None:
    shadow = tmp_path / "shadow.json"
    history = tmp_path / "history.jsonl"
    output = tmp_path / "output.json"
    shadow_fill = {
        "member": "capital_allocation_challenger",
        "symbol": "WFC",
        "side": "BUY",
        "qty": 5,
        "reason": "signal_t_to_open_t_plus_1",
        "raw_price": 80.635002,
        "fill_price": 80.659193,
        "costs": 1.169522,
        "shadow_event": "OPEN_FILL",
    }
    official_fill = {
        **shadow_fill,
        "raw_price": 80.474998,
        "fill_price": 80.499141,
        "costs": 1.167201,
    }
    official_fill.pop("shadow_event")
    _write(
        shadow,
        {
            "market_session": "2026-10-07",
            "quote_source": {"provider": "Yahoo Finance chart", "interval": "5m"},
            "shadow_open_fills": [shadow_fill],
            "shadow_exits": [],
        },
    )
    _write(history, {"session": "2026-10-07", "trades": [official_fill]})
    result = reconcile(shadow_path=shadow, history_path=history, output_path=output)
    assert result["status"] == "RECONCILED"
    assert result["matched_trade_count"] == 1
    assert result["explained_difference_counts"] == {
        "verified_5m_vs_daily_open_source_variance": 1
    }
    assert result["explained_differences"][0]["price_bps"] > 19
    assert result["divergences"] == []


@pytest.mark.parametrize(
    ("mutation", "value"),
    [
        ("source", "unverified"),
        ("interval", "15m"),
        ("shadow_event", "INTRADAY_EXIT"),
        ("shadow_fill", 81.5),
        ("qty", 6),
    ],
)
def test_unverified_open_price_difference_remains_fail_closed(
    tmp_path: Path, mutation: str, value: object
) -> None:
    shadow = tmp_path / "shadow.json"
    history = tmp_path / "history.jsonl"
    output = tmp_path / "output.json"
    original = {
        "member": "capital_allocation_challenger",
        "symbol": "WFC",
        "side": "BUY",
        "qty": 5,
        "reason": "signal_t_to_open_t_plus_1",
        "raw_price": 80.635002,
        "fill_price": 80.659193,
        "costs": 1.169522,
        "shadow_event": "OPEN_FILL",
    }
    official = {**original, "raw_price": 80.474998, "fill_price": 80.499141, "costs": 1.167201}
    official.pop("shadow_event")
    feed = {"provider": "Yahoo Finance chart", "interval": "5m"}
    if mutation == "source":
        feed["provider"] = str(value)
    elif mutation == "interval":
        feed["interval"] = str(value)
    elif mutation == "shadow_event":
        original["shadow_event"] = str(value)
    elif mutation == "shadow_fill":
        original["fill_price"] = float(value)
    elif mutation == "qty":
        original["qty"] = int(value)
    _write(shadow, {
        "market_session": "2026-10-07",
        "quote_source": feed,
        "shadow_open_fills": [original],
        "shadow_exits": [],
    })
    _write(history, {"session": "2026-10-07", "trades": [official]})
    with pytest.raises(RuntimeError, match="Material shadow/EOD divergence"):
        reconcile(shadow_path=shadow, history_path=history, output_path=output)
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "DIVERGED"


@pytest.mark.parametrize(
    ("session", "symbol", "qty", "shadow_open", "official_open",
     "shadow_fill", "official_fill", "shadow_cost", "official_cost",
     "high", "low", "candle_close"),
    [
        ("2026-10-15", "KO", 9, 93.419998, 93.150002,
         93.448024, 93.177947, 2.438918, 2.431869,
         93.870003, 93.099998, 93.669998),
        ("2026-10-16", "UNH", 1, 408.290009, 410.350006,
         408.412496, 410.473111, 1.184359, 1.190335,
         412.369995, 401.790009, 402.0),
    ],
)
def test_actual_october_yahoo_open_discrepancies_are_proven_by_first_candle(
    tmp_path: Path, session: str, symbol: str, qty: int,
    shadow_open: float, official_open: float, shadow_fill: float,
    official_fill: float, shadow_cost: float, official_cost: float,
    high: float, low: float, candle_close: float,
) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    at = datetime.fromisoformat(session + "T09:30:00").replace(
        tzinfo=ZoneInfo("America/New_York")
    ).isoformat()
    shadow_trade = {
        "member": "mean_reversion_floor_w1", "symbol": symbol, "side": "BUY",
        "qty": qty, "reason": "signal_t_to_open_t_plus_1",
        "shadow_event": "OPEN_FILL",
        "raw_price": shadow_open, "fill_price": shadow_fill, "costs": shadow_cost,
    }
    official_trade = {**shadow_trade, "raw_price": official_open,
                      "fill_price": official_fill, "costs": official_cost}
    official_trade.pop("shadow_event")
    sh = tmp_path / "shadow.json"
    h = tmp_path / "history.jsonl"
    out = tmp_path / "audit.json"
    _write(sh, {
        "market_session": session,
        "quote_source": {"provider": "Yahoo Finance chart", "interval": "5m"},
        "open_quote_evidence": {
            symbol: {
                "market_session": session, "timestamp": at, "interval": "5m",
                "open": shadow_open, "high": high, "low": low, "close": candle_close,
            }
        },
        "shadow_open_fills": [shadow_trade],
        "shadow_exits": [],
    })
    _write(h, {"session": session, "trades": [official_trade]})
    actual = reconcile(shadow_path=sh, history_path=h, output_path=out)
    assert actual["status"] == "RECONCILED"
    assert actual["matched_trade_count"] == 1
    assert actual["divergence_counts"] == {}
    assert actual["explained_difference_counts"] == {
        "verified_5m_open_candle_daily_open_envelope": 1
    }
    proof = actual["explained_differences"][0]["opening_bar_provenance"]
    assert proof["candle_low"] <= official_open <= proof["candle_high"]
    assert actual["explained_differences"][0]["price_bps"] > 25


@pytest.mark.parametrize("mutation", [
    "missing_proof", "invalid_date", "delayed_candle", "bad_interval",
    "daily_open_outside_candle", "wrong_intraday_open", "over_100bps",
    "cost_mismatch", "execution_mismatch", "wrong_provider",
])
def test_large_open_discrepancies_without_full_provenance_fail_closed(
    tmp_path: Path, mutation: str,
) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    session = "2026-10-16"
    timestamp = datetime(2026, 10, 16, 9, 30, tzinfo=ZoneInfo(
        "America/New_York"
    )).isoformat()
    trade = {
        "member": "mean_reversion_floor_w1", "symbol": "UNH",
        "side": "BUY", "qty": 1, "reason": "signal_t_to_open_t_plus_1",
        "shadow_event": "OPEN_FILL", "raw_price": 408.290009,
        "fill_price": 408.412496, "costs": 1.184359,
    }
    official = {**trade, "raw_price": 410.350006,
                "fill_price": 410.473111, "costs": 1.190335}
    official.pop("shadow_event")
    candle = {
        "market_session": session, "timestamp": timestamp, "interval": "5m",
        "open": 408.290009, "low": 401.790009, "high": 412.369995, "close": 402.,
    }
    quote_source = {"provider": "Yahoo Finance chart", "interval": "5m"}
    if mutation == "missing_proof":
        candle = None
    elif mutation == "invalid_date":
        candle["market_session"] = "2026-10-15"
    elif mutation == "delayed_candle":
        candle["timestamp"] = timestamp.replace("09:30", "09:35")
    elif mutation == "bad_interval":
        candle["interval"] = "15m"
    elif mutation == "daily_open_outside_candle":
        candle["high"] = 410.0
    elif mutation == "wrong_intraday_open":
        candle["open"] = 407.0
    elif mutation == "over_100bps":
        official["raw_price"] = 420.
        official["fill_price"] = 420.126
        candle["high"] = 422.
    elif mutation == "cost_mismatch":
        official["costs"] = 2.
    elif mutation == "execution_mismatch":
        official["fill_price"] = 411.
    elif mutation == "wrong_provider":
        quote_source["provider"] = "Unknown"
    sh, h, out = (tmp_path / name for name in ("s.json", "h.jsonl", "out.json"))
    _write(sh, {
        "market_session": session,
        "quote_source": quote_source,
        "open_quote_evidence": {"UNH": candle} if candle else {},
        "shadow_open_fills": [trade],
        "shadow_exits": [],
    })
    _write(h, {"session": session, "trades": [official]})
    with pytest.raises(RuntimeError, match="Material shadow/EOD divergence"):
        reconcile(shadow_path=sh, history_path=h, output_path=out)
    assert json.loads(out.read_text())["status"] == "DIVERGED"


def test_one_share_daily_vs_5m_rebalance_is_proven_by_two_execution_replays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from league import shadow_reconcile
    from league.engine import _execute_target
    import copy

    session = "2026-10-16"
    member = {
        "id": "weekly_opportunity_ridge",
        "cash": 200.,
        "positions": {"AMAT": {"qty": 3, "last_price": 420.26}},
        "pending_targets": {"AMAT": {"weight": 0.8632}},
    }
    cfg = {"execution": {
        "commission_bps": 26, "slippage_bps": 3, "sell_fee_bps": 3,
        "min_rebalance_weight_delta": 0.02,
        "min_rebalance_notional_usd": 100.,
    }}
    five_bars = {"AMAT": {"open": 421.16}}
    official_bars = {"AMAT": {"open": 420.26}}
    def simulate(bars):
        rows = []
        _execute_target(
            copy.deepcopy(member), member["pending_targets"],
            bars, cfg["execution"], rows, session=session, session_number=10,
        )
        return rows
    shadow_fill = {**simulate(five_bars)[0], "shadow_event": "OPEN_FILL"}
    assert simulate(official_bars) == []

    base = {
        "official_state_hash": "original-2026-10-15",
        "league_id": "test-league", "last_eod_session": "2026-10-15",
        "sessions": 9, "members": {"weekly_opportunity_ridge": member},
    }
    sh = tmp_path / "sh.json"
    h = tmp_path / "history.jsonl"
    out = tmp_path / "out.json"
    b = tmp_path / "base.json"
    c = tmp_path / "cfg.json"
    db = tmp_path / "market.sqlite"
    db.touch()
    _write(b, base)
    _write(c, cfg)
    _write(sh, {
        "market_session": session, "league_id": "test-league",
        "source_base_state_hash": base["official_state_hash"],
        "quote_source": {"provider": "Yahoo Finance chart", "interval": "5m"},
        "open_quote_evidence": {"AMAT": {
            "interval": "5m", "market_session": session,
            "timestamp": "2026-10-16T13:30:00+00:00",
            "open": 421.16, "high": 422., "low": 420., "close": 420.5,
        }},
        "shadow_open_fills": [shadow_fill], "shadow_exits": [],
    })
    official_exit = {
        "member": "weekly_opportunity_ridge", "symbol": "AMAT",
        "side": "SELL", "qty": 3,
        "reason": "max_holding_sessions_10", "fill_price": 409.0,
    }
    _write(h, {"session": session, "trades": [official_exit]})
    monkeypatch.setattr(shadow_reconcile, "load_daily_bars", lambda *args: [{
        "symbol": "AMAT", "timestamp": "2026-10-16T13:30:00+00:00",
        "open": 420.26, "high": 423., "low": 419.,
        "close": 420.5, "volume": 1000.,
    }])
    def reconcile_case():
        return reconcile(
            shadow_path=sh, history_path=h, output_path=out,
            base_path=b, league_config_path=c, market_db=db,
        )

    first = reconcile_case()
    assert first["status"] == "RECONCILED"
    assert first["matched_trade_count"] == 0
    assert first["explained_difference_counts"] == {
        "eod_only_holding_exit": 1,
        "verified_one_share_open_rebalance_quantization": 1,
    }
    assert first["divergences"] == []
    assert first["explained_differences"][0]["shadow_open_replay_verified"]
    assert first["explained_differences"][0]["official_open_replay_verified"]

    # All missing/contradictory proof must remain material.
    data = json.loads(sh.read_text())
    data["open_quote_evidence"] = {}
    _write(sh, data)
    with pytest.raises(RuntimeError, match="missing_official_trade"):
        reconcile_case()
    data["open_quote_evidence"]["AMAT"] = {
        "interval": "5m", "market_session": session,
        "timestamp": "2026-10-16T13:30:00+00:00",
        "open": 421.16, "high": 422., "low": 420., "close": 420.5,
    }
    _write(sh, data)
    baseline = json.loads(b.read_text())
    baseline["official_state_hash"] = "unrelated-hash"
    _write(b, baseline)
    with pytest.raises(RuntimeError, match="missing_official_trade"):
        reconcile_case()
