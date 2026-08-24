from __future__ import annotations

import json
from pathlib import Path

import pytest

from league.engine import (
    advance_league,
    build_leaderboard,
    initialize_league,
    load_state,
)
from strategies.run_strategies import load_simple_yaml


def _cfg() -> dict:
    return {
        "league_id": "test_league_v1",
        "initial_nav_usd": 100000.0,
        "execution": {
            "commission_bps": 2.0,
            "slippage_bps": 3.0,
            "sell_fee_bps": 3.0,
        },
        "members": [
            {"id": "weekly_opportunity_ridge"},
            {"id": "breakout_protected_by_floor"},
            {"id": "benchmark_spy"},
            {"id": "benchmark_equal_weight"},
        ],
        "promotion_review": {
            "min_sessions": 63,
            "min_trades": 10,
            "max_drawdown_abs": 0.15,
            "min_sharpe": 0.5,
        },
    }


def _contract() -> dict[str, str]:
    return {
        "league_config_sha256": "league",
        "strategies_config_sha256": "strategies",
        "weekly_model_sha256": "weekly",
    }


def _targets() -> dict[str, dict[str, dict]]:
    return {
        "weekly_opportunity_ridge": {
            "AAA": {"weight": 0.20, "stop_price": 80.0, "take_profit_price": 130.0}
        },
        "breakout_protected_by_floor": {},
        "benchmark_spy": {"SPY": {"weight": 1.0}},
        "benchmark_equal_weight": {"AAA": {"weight": 1.0}},
    }


def _bars(open_price: float = 100.0, close_price: float = 110.0) -> dict[str, dict]:
    return {
        "AAA": {
            "open": open_price,
            "high": max(open_price, close_price) + 1.0,
            "low": min(open_price, close_price) - 1.0,
            "close": close_price,
        },
        "SPY": {
            "open": open_price,
            "high": max(open_price, close_price) + 1.0,
            "low": min(open_price, close_price) - 1.0,
            "close": close_price,
        },
    }


def test_genesis_gives_every_member_same_100k_and_does_not_trade_same_day(tmp_path: Path) -> None:
    state = initialize_league(tmp_path, _cfg(), "2026-08-24", _contract(), _targets())

    assert state["start_session"] == "2026-08-24"
    assert state["session_count"] == 1
    for member in state["members"].values():
        assert member["cash"] == 100000.0
        assert member["positions"] == {}
        assert member["daily_nav"] == [{"session": "2026-08-24", "nav": 100000.0}]
        assert member["trade_count"] == 0


def test_signal_generated_at_t_executes_at_next_session_open(tmp_path: Path) -> None:
    state = initialize_league(tmp_path, _cfg(), "2026-08-24", _contract(), _targets())
    state = advance_league(
        tmp_path,
        state,
        _cfg(),
        "2026-08-25",
        _bars(),
        _contract(),
        {},
    )

    weekly = state["members"]["weekly_opportunity_ridge"]
    assert weekly["trade_count"] == 1
    assert weekly["positions"]["AAA"]["qty"] > 0
    assert weekly["daily_nav"][-1]["nav"] > 100000.0
    assert state["members"]["benchmark_spy"]["trade_count"] == 1


def test_same_session_rerun_is_idempotent(tmp_path: Path) -> None:
    state = initialize_league(tmp_path, _cfg(), "2026-08-24", _contract(), _targets())
    first_hash = state["last_hash"]
    state = advance_league(
        tmp_path,
        state,
        _cfg(),
        "2026-08-24",
        _bars(),
        _contract(),
        {},
    )

    assert state["last_hash"] == first_hash
    assert len((tmp_path / "history.jsonl").read_text(encoding="utf-8").splitlines()) == 1


def test_hash_chain_detects_retroactive_history_change(tmp_path: Path) -> None:
    initialize_league(tmp_path, _cfg(), "2026-08-24", _contract(), _targets())
    history = tmp_path / "history.jsonl"
    history.write_text(
        history.read_text(encoding="utf-8").replace('"event": "GENESIS"', '"event": "ALTERED"'),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="audit chain broken"):
        load_state(tmp_path)


def test_frozen_contract_change_requires_new_league(tmp_path: Path) -> None:
    state = initialize_league(tmp_path, _cfg(), "2026-08-24", _contract(), _targets())
    changed = {**_contract(), "weekly_model_sha256": "different"}

    with pytest.raises(RuntimeError, match="frozen contract changed"):
        advance_league(
            tmp_path,
            state,
            _cfg(),
            "2026-08-25",
            _bars(),
            changed,
            {},
        )


def test_promotion_cannot_pass_before_minimum_prospective_sessions(tmp_path: Path) -> None:
    state = initialize_league(tmp_path, _cfg(), "2026-08-24", _contract(), _targets())
    leaderboard = build_leaderboard(state, _cfg())
    strategies = {
        row["strategy"]: row for row in leaderboard["rows"] if not row["strategy"].startswith("benchmark_")
    }

    assert leaderboard["automatic_promotion"] is False
    assert leaderboard["live_execution_enabled"] is False
    assert all(row["promotion_review_eligible"] is False for row in strategies.values())
    assert all(row["promotion_checks"]["min_sessions"] is False for row in strategies.values())


def test_operational_paper_and_live_gates_remain_disabled() -> None:
    config = load_simple_yaml(Path("config/strategies.yaml"))

    assert config["activation"]["paper_execution_enabled"] is False
    assert config["activation"]["live_execution_enabled"] is False
    for strategy in config["strategies"].values():
        assert strategy["paper_enabled"] is False
        assert strategy["live_enabled"] is False
        assert strategy["canonical_serving_enabled"] is False
