import json
from pathlib import Path

import pytest

from league.attribution import build_attribution_report, build_operations_ranking


def test_attribution_tracks_sources_realized_pnl_and_heat(tmp_path: Path) -> None:
    history = tmp_path / "history.jsonl"
    records = [
        {
            "session": "2026-01-01",
            "trades": [],
            "decisions_for_next_open": {
                "capital_allocation_challenger": {
                    "AAA": {
                        "weight": 0.2,
                        "source_strategy": "weekly_opportunity_ridge",
                        "source_strategies": ["weekly_opportunity_ridge", "mean_reversion_floor_w1"],
                        "consensus_count": 2,
                        "score": 0.8,
                        "risk_budget_pct_nav": 0.007,
                        "stop_risk_pct": 0.05,
                    }
                }
            },
            "state_after": {
                "members": {
                    "capital_allocation_challenger": {
                        "cash": 10000.0,
                        "positions": {},
                    }
                }
            },
        },
        {
            "session": "2026-01-02",
            "trades": [
                {
                    "member": "capital_allocation_challenger",
                    "symbol": "AAA",
                    "side": "BUY",
                    "qty": 10,
                    "fill_price": 100.0,
                    "costs": 2.0,
                    "reason": "signal_t_to_open_t_plus_1",
                }
            ],
            "decisions_for_next_open": {},
            "state_after": {
                "members": {
                    "capital_allocation_challenger": {
                        "cash": 8998.0,
                        "positions": {
                            "AAA": {"qty": 10, "last_price": 102.0, "stop_price": 95.0}
                        },
                    }
                }
            },
        },
        {
            "session": "2026-01-03",
            "trades": [
                {
                    "member": "capital_allocation_challenger",
                    "symbol": "AAA",
                    "side": "SELL",
                    "qty": 10,
                    "fill_price": 110.0,
                    "costs": 2.0,
                    "reason": "take_profit_touched",
                }
            ],
            "decisions_for_next_open": {},
            "state_after": {
                "members": {
                    "capital_allocation_challenger": {
                        "cash": 10096.0,
                        "positions": {},
                    }
                }
            },
        },
    ]
    history.write_text("\n".join(json.dumps(row) for row in records) + "\n", encoding="utf-8")
    report = build_attribution_report(history)
    assert report["status"] == "OK"
    assert report["summary"]["realized_round_trips"] == 1
    assert report["summary"]["realized_net_pnl"] == pytest.approx(96.0)
    assert report["summary"]["win_rate"] == pytest.approx(1.0)
    trade = report["realized_trades"][0]
    assert trade["holding_sessions"] == 2
    assert trade["net_pnl_per_session"] == pytest.approx(48.0)
    assert trade["net_return"] == pytest.approx(96.0 / 1002.0)
    by_source = {row["source_strategy"]: row for row in report["source_attribution"]}
    assert by_source["weekly_opportunity_ridge"]["net_pnl_equal_split"] == pytest.approx(48.0)
    assert by_source["mean_reversion_floor_w1"]["net_pnl_equal_split"] == pytest.approx(48.0)
    assert report["exposure"]["gross_exposure_pct_nav"] == pytest.approx(0.0)


def test_missing_history_returns_waiting(tmp_path: Path) -> None:
    report = build_attribution_report(tmp_path / "missing.jsonl")
    assert report["status"] == "WAITING"



def test_operations_ranking_uses_net_pnl_per_session_across_strategies(tmp_path: Path) -> None:
    history = tmp_path / "history.jsonl"
    records = [
        {
            "session": "2026-01-01",
            "trades": [],
            "decisions_for_next_open": {},
            "state_after": {"members": {}},
        },
        {
            "session": "2026-01-02",
            "trades": [
                {"member": "fast_strategy", "symbol": "AAA", "side": "BUY", "qty": 10, "fill_price": 100.0, "costs": 1.0},
                {"member": "slow_strategy", "symbol": "BBB", "side": "BUY", "qty": 10, "fill_price": 100.0, "costs": 1.0},
                {"member": "loss_strategy", "symbol": "CCC", "side": "BUY", "qty": 10, "fill_price": 100.0, "costs": 1.0},
                {"member": "benchmark_spy", "symbol": "SPY", "side": "BUY", "qty": 10, "fill_price": 100.0, "costs": 1.0},
            ],
            "decisions_for_next_open": {},
            "state_after": {"members": {}},
        },
        {
            "session": "2026-01-03",
            "trades": [
                {"member": "fast_strategy", "symbol": "AAA", "side": "SELL", "qty": 10, "fill_price": 106.0, "costs": 1.0},
                {"member": "loss_strategy", "symbol": "CCC", "side": "SELL", "qty": 10, "fill_price": 95.0, "costs": 1.0},
                {"member": "benchmark_spy", "symbol": "SPY", "side": "SELL", "qty": 10, "fill_price": 120.0, "costs": 1.0},
            ],
            "decisions_for_next_open": {},
            "state_after": {"members": {}},
        },
        {
            "session": "2026-01-04",
            "trades": [
                {"member": "slow_strategy", "symbol": "BBB", "side": "SELL", "qty": 10, "fill_price": 108.0, "costs": 1.0},
            ],
            "decisions_for_next_open": {},
            "state_after": {"members": {}},
        },
    ]
    history.write_text("\n".join(json.dumps(row) for row in records) + "\n", encoding="utf-8")

    ranking = build_operations_ranking(
        history,
        ["fast_strategy", "slow_strategy", "loss_strategy"],
        top_n=5,
    )

    assert ranking["status"] == "OK"
    assert ranking["realized_operations"] == 3
    assert [row["strategy"] for row in ranking["top_operations"]] == [
        "fast_strategy",
        "slow_strategy",
    ]
    assert ranking["top_operations"][0]["net_pnl_per_session"] > ranking["top_operations"][1]["net_pnl_per_session"]
    assert ranking["bottom_operations"][0]["strategy"] == "loss_strategy"
    assert all(row["strategy"] != "benchmark_spy" for row in ranking["top_operations"])


def test_operations_ranking_tracks_history_and_open_losing_positions(tmp_path: Path) -> None:
    history = tmp_path / "history.jsonl"
    records = [
        {
            "session": "2026-01-05",
            "trades": [],
            "decisions_for_next_open": {},
            "state_after": {
                "members": {
                    "test_strategy": {"cash": 10000.0, "positions": {}},
                }
            },
        },
        {
            "session": "2026-01-06",
            "trades": [
                {"member": "test_strategy", "symbol": "OPEN", "side": "BUY", "qty": 10, "fill_price": 100.0, "costs": 1.0},
                {"member": "test_strategy", "symbol": "CLOSED", "side": "BUY", "qty": 5, "fill_price": 50.0, "costs": 0.5},
            ],
            "decisions_for_next_open": {},
            "state_after": {
                "members": {
                    "test_strategy": {
                        "cash": 8748.5,
                        "positions": {
                            "OPEN": {"qty": 10, "cost_basis": 100.0, "last_price": 95.0, "stop_price": 85.0},
                            "CLOSED": {"qty": 5, "cost_basis": 50.0, "last_price": 50.0},
                        },
                    }
                }
            },
        },
        {
            "session": "2026-01-07",
            "trades": [
                {"member": "test_strategy", "symbol": "CLOSED", "side": "SELL", "qty": 5, "fill_price": 60.0, "costs": 0.5},
            ],
            "decisions_for_next_open": {},
            "state_after": {
                "members": {
                    "test_strategy": {
                        "cash": 9048.0,
                        "positions": {
                            "OPEN": {"qty": 10, "cost_basis": 100.0, "last_price": 90.0, "stop_price": 85.0},
                        },
                    }
                }
            },
        },
    ]
    history.write_text("\n".join(json.dumps(row) for row in records) + "\n", encoding="utf-8")

    ranking = build_operations_ranking(history, ["test_strategy"], top_n=10)

    assert ranking["status"] == "OK"
    assert len(ranking["operations_history"]) == 3
    assert ranking["operations_history"][-1]["session"] == "2026-01-07"
    assert ranking["operations_history"][-1]["net_pnl"] == pytest.approx(49.0)
    assert ranking["operations_history"][-1]["cumulative_net_pnl"] == pytest.approx(49.0)

    assert ranking["open_losing_positions_count"] == 1
    assert ranking["open_losing_unrealized_pnl"] == pytest.approx(-101.0)
    open_loss = ranking["open_losing_positions"][0]
    assert open_loss["strategy"] == "test_strategy"
    assert open_loss["symbol"] == "OPEN"
    assert open_loss["holding_sessions"] == 2
    assert open_loss["unrealized_pnl"] == pytest.approx(-101.0)
    assert open_loss["unrealized_return"] == pytest.approx(-101.0 / 1001.0)
