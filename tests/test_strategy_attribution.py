import json
from pathlib import Path

import pytest

from league.attribution import build_attribution_report


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
    by_source = {row["source_strategy"]: row for row in report["source_attribution"]}
    assert by_source["weekly_opportunity_ridge"]["net_pnl_equal_split"] == pytest.approx(48.0)
    assert by_source["mean_reversion_floor_w1"]["net_pnl_equal_split"] == pytest.approx(48.0)
    assert report["exposure"]["gross_exposure_pct_nav"] == pytest.approx(0.0)


def test_missing_history_returns_waiting(tmp_path: Path) -> None:
    report = build_attribution_report(tmp_path / "missing.jsonl")
    assert report["status"] == "WAITING"
