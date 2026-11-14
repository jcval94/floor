import json
from copy import deepcopy
import pytest

pytest.importorskip("mcp")
pytest.importorskip("exchange_calendars")
import exchange_calendars as xc
from ai_lab.contracts import Plan
from ai_lab.store import Store
from ai_lab.strategy_replay import (
    REQUIRED_INPUTS,
    forecast_inputs,
    replay_strategy,
    validate_strategy_inputs,
)
from league.engine import load_state
from strategies.base import StrategyDecision


@pytest.fixture
def sample():
    calendar = xc.get_calendar("XNYS")
    days = calendar.sessions_in_range("2026-02-02", "2026-10-16")
    rows = []
    for day in days:
        ts = str(day.date()) + "T23:59:00+00:00"
        for symbol in ("GOOGL", "MSFT", "SPY"):
            r = {f: 0.01 for f in REQUIRED_INPUTS}
            r.update(
                symbol=symbol,
                timestamp=ts,
                available_at=ts,
                open=100.0,
                high=102.0,
                low=98.0,
                close=100.0,
                volume=1e6,
                atr_14=2.0,
                floor_w1=97.0,
                ceiling_w1=104.0,
                target_end_date_w1=(day + __import__("pandas").Timedelta(days=7))
                .date()
                .isoformat()
                + "T23:59:00+00:00",
                horizon_complete_w1=True,
                feature_available_at={f: ts for f in REQUIRED_INPUTS},
            )
            rows.append(r)
    manifest = {
        "target": "floor_w1",
        "horizon_sessions": 5,
        "features": ["atr_14"],
        "materialized_features": list(REQUIRED_INPUTS),
        "feature_definition_version": "floor-feature-builder/full-v2-benchmark-joined",
        "temporal_evidence": "synthetic_fixture",
    }
    plan = Plan(
        question="Compara mean reversion y breakout",
        recipe="strategy_comparison",
        dataset_id="0" * 64,
        features=["atr_14"],
        objective="net_return",
        strategies=["mean_reversion_floor_w1"],
    )
    return rows, manifest, plan


def fake_forecasts(market_rows, *args, **kwargs):
    return {
        "blocked": [],
        "forecasts": [
            {
                **r,
                "floor_d1": 95.0,
                "ceiling_d1": 110.0,
                "floor_w1": 94.0,
                "ceiling_w1": 112.0,
                "risk_floor_d1": 90.0,
                "risk_ceiling_d1": 115.0,
                "risk_geometry_available_d1": True,
                "risk_floor_w1": 90.0,
                "risk_ceiling_w1": 115.0,
                "risk_geometry_available_w1": True,
            }
            for r in market_rows
        ],
    }


def install_first_buy_then_hold(monkeypatch):
    import ai_lab.strategy_replay as replay

    calls = [0]

    def decisions(rows, cfg, strategy, session):
        calls[0] += 1
        first = calls[0] == 1
        return [
            StrategyDecision(
                "mean_reversion_floor_w1",
                r["symbol"],
                "BUY" if first and r["symbol"] == "GOOGL" else "HOLD",
                1.0,
                5 if first and r["symbol"] == "GOOGL" else 0,
                "w1",
                "controlled fixture",
                "fixture",
                80.0,
                9999.0,
                0.05,
                0.1,
                0.5,
            )
            for r in rows
        ]

    monkeypatch.setattr(replay, "generate_forecasts", fake_forecasts)
    monkeypatch.setitem(replay.STRATEGIES, "mean_reversion_floor_w1", decisions)


def test_reuses_next_open_engine_and_post_exit_hold_state(
    sample, tmp_path, monkeypatch
):
    rows, m, p = sample
    install_first_buy_then_hold(monkeypatch)
    result = replay_strategy(rows, m, p, tmp_path / "ledger")
    trades = [t for t in result["trades"] if t["member"] == "mean_reversion_floor_w1"]
    assert len([t for t in trades if t["side"] == "BUY"]) == 1
    assert len([t for t in trades if t["side"] == "SELL"]) == 1
    buy = next(t for t in trades if t["side"] == "BUY")
    assert buy["reason"] == "signal_t_to_open_t_plus_1"
    assert buy["session"] > result["comparability"]["start"]
    sell = next(t for t in trades if t["side"] == "SELL")
    assert sell["reason"] == "max_holding_sessions_5"
    restored = load_state(tmp_path / "ledger")
    assert restored is not None and all(
        not r["positions"] for r in restored["members"].values()
    )
    assert result["cost_contract"]["round_trip_cost_bps"] == 61
    assert all(r["promotion_eligible"] is False for r in result["metrics"])
    assert result["ranking_leader"]["strategy"] is None


def test_stop_gap_uses_open_and_conservative_existing_exit(
    sample, tmp_path, monkeypatch
):
    rows, m, p = sample
    install_first_buy_then_hold(monkeypatch)
    _, _, _, _, dates, _ = validate_strategy_inputs(rows, m, p)
    for r in rows:
        if r["symbol"] == "GOOGL" and r["timestamp"][:10] == dates[2]:
            r.update(open=75.0, low=70.0, high=110.0, close=100.0)
    result = replay_strategy(rows, m, p, tmp_path / "gap")
    gaps = [
        t
        for t in result["trades"]
        if t["member"] == "mean_reversion_floor_w1"
        and t["reason"] == "stop_gap_through_at_open"
    ]
    assert len(gaps) == 1 and gaps[0]["raw_price"] == 75.0


def test_labels_are_not_forecast_or_strategy_inputs(sample):
    rows, _, _ = sample
    clean = forecast_inputs(rows, "2026-10-01", ["GOOGL", "MSFT"])
    assert clean
    assert all(
        "floor_w1" not in r
        and "target_end_date_w1" not in r
        and "horizon_complete_w1" not in r
        for r in clean
    )


def test_validation_blocks_gaps_future_availability_and_cost_drift(sample, monkeypatch):
    import ai_lab.strategy_replay as replay

    rows, m, p = sample
    bad = [
        r
        for r in rows
        if not (r["symbol"] == "SPY" and r["timestamp"].startswith("2026-10-01"))
    ]
    with pytest.raises(ValueError, match="INCOMPLETE_SESSION_UNIVERSE"):
        validate_strategy_inputs(bad, m, p)
    future = deepcopy(rows)
    next(r for r in future if r["timestamp"].startswith("2026-10-01"))[
        "feature_available_at"
    ]["atr_14"] = "2027-01-01T00:00:00+00:00"
    with pytest.raises(ValueError, match="FEATURE_AVAILABILITY"):
        validate_strategy_inputs(future, m, p)
    original = replay.load_strategy_runtime_config

    def drift():
        cfg = original()
        cfg["costs"]["slippage_bps"] = 20.0
        return cfg

    monkeypatch.setattr(replay, "load_strategy_runtime_config", drift)
    with pytest.raises(ValueError, match="COST_CONTRACT_DRIFT"):
        validate_strategy_inputs(rows, m, p)


def test_new_strategy_field_preserves_old_recipe_idempotency(sample, tmp_path):
    rows, m, _ = sample
    s = Store(tmp_path)
    dataset = s.register(rows, m)
    p = Plan(
        question="Compara los modelos registrados",
        recipe="model_comparison",
        dataset_id=dataset,
        features=["atr_14"],
    )
    run = s.submit(p, "operator", "contract-upgrade")
    with s.transaction() as c:
        raw = json.loads(
            c.execute(
                "SELECT plan FROM runs WHERE run_id=?", (run["run_id"],)
            ).fetchone()[0]
        )
        raw.pop("strategies")
        c.execute(
            "UPDATE runs SET plan=? WHERE run_id=?", (json.dumps(raw), run["run_id"])
        )
    assert s.submit(p, "operator", "contract-upgrade")["run_id"] == run["run_id"]


def test_strategy_report_and_slack_format_are_measured_and_safe(
    sample, tmp_path, monkeypatch
):
    from ai_lab.execute import strategy_report
    from ai_lab.slack import post_message

    rows, m, p = sample
    install_first_buy_then_hold(monkeypatch)
    result = replay_strategy(rows, m, p, tmp_path / "report-ledger")
    text = strategy_report(p, m, result)
    assert text.count("<polyline") == len(result["metrics"])
    assert "OOS no verificado" in text and "Ledger hash-chain" in text
    captured = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, *args):
            return b'{"ok":true,"ts":"fixture-ts"}'

    def fake_open(request, timeout):
        captured.append(json.loads(request.data))
        return FakeResponse()

    import ai_lab.slack as slack

    monkeypatch.setattr(slack.urllib.request, "urlopen", fake_open)
    from ai_lab.store import canonical

    event = {
        "payload": canonical(
            {
                "result": {
                    "metrics": result["metrics"],
                    "conclusion": result["conclusion"],
                }
            }
        ),
        "kind": "SUCCEEDED",
        "run_id": "fixture",
        "event_id": "fixture-event",
    }
    assert post_message("fixture-token", "fixture-channel", event) == "fixture-ts"
    assert "turnover=" in captured[0]["text"] and "drawdown=" in captured[0]["text"]
    assert "fixture-token" not in captured[0]["text"]


def test_wrong_task_objective_rejected_before_queue(sample, tmp_path):
    rows, m, _ = sample
    s = Store(tmp_path)
    dataset = s.register(rows, m)
    plan = Plan(
        question="Compara los modelos registrados",
        recipe="model_comparison",
        dataset_id=dataset,
        features=["atr_14"],
        objective="net_return",
    )
    with pytest.raises(ValueError, match="OBJECTIVE_NOT_VALID_FOR_RECIPE"):
        s.submit(plan, "operator", "wrong-objective")
    with s.connect() as c:
        assert c.execute("SELECT count(*) FROM runs").fetchone()[0] == 0


def test_nonfinite_features_and_same_total_cost_component_drift_blocked(
    sample, monkeypatch
):
    import ai_lab.strategy_replay as replay

    rows, m, p = sample
    bad = deepcopy(rows)
    next(r for r in bad if r["timestamp"].startswith("2026-10-01"))["momentum_20"] = (
        "nan"
    )
    with pytest.raises(ValueError, match="FEATURE_AVAILABILITY_INVALID"):
        validate_strategy_inputs(bad, m, p)
    original = replay.load_strategy_runtime_config

    def drift():
        cfg = original()
        cfg["costs"]["commission_bps"] = 0.0
        return cfg

    monkeypatch.setattr(replay, "load_strategy_runtime_config", drift)
    with pytest.raises(ValueError, match="COST_CONTRACT_COMPONENT_DRIFT"):
        validate_strategy_inputs(rows, m, p)
