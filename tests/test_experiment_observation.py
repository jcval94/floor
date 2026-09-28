from __future__ import annotations

import json
from pathlib import Path

from league.experiment_observation import build_experiment_observation


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def _leaderboard(
    last_session: str = "2026-08-25",
    sessions: int = 2,
    *,
    league_id: str = "strategy_league_v1",
    start_session: str = "2026-08-24",
) -> dict:
    return {
        "schema_version": 1,
        "league_id": league_id,
        "status": "RUNNING",
        "start_session": start_session,
        "last_session": last_session,
        "sessions": sessions,
        "initial_nav_usd": 100000.0,
        "automatic_promotion": False,
        "live_execution_enabled": False,
        "rows": [
            {
                "strategy": "weekly_opportunity_ridge",
                "nav": 101000.0,
                "return": 0.01,
                "vs_spy": 0.004,
            }
        ],
    }


def test_observation_aggregates_only_post_genesis_model_evidence(tmp_path: Path) -> None:
    data = tmp_path / "data"
    league_root = data / "metrics" / "strategy_league"
    _write_json(league_root / "leaderboard.json", _leaderboard())
    _write_json(
        league_root / "models" / "weekly_opportunity_challenger.json",
        {
            "model_name": "weekly_opportunity_ridge",
            "version": "league-v1",
            "metrics": {
                "spearman_rank_correlation": 0.25,
                "top_quintile_return_lift": 0.03,
            },
        },
    )

    _write_jsonl(
        data / "predictions" / "AAA.jsonl",
        [
            {
                "batch_id": "2026-08-24:CLOSE",
                "symbol": "AAA",
                "horizon": "d1",
                "as_of": "2026-08-24T20:00:00+00:00",
                "model_version": "v1",
            },
            {
                "batch_id": "2026-08-25:CLOSE",
                "symbol": "AAA",
                "horizon": "d1",
                "as_of": "2026-08-25T20:00:00+00:00",
                "model_version": "v2",
            },
        ],
    )
    _write_jsonl(
        data / "predictions" / "reconciliations" / "AAA.jsonl",
        [
            {
                "prediction_key": "old-before-genesis",
                "symbol": "AAA",
                "horizon": "d1",
                "predicted_as_of": "2026-08-20T20:00:00+00:00",
                "model_version": "old",
                "predicted_floor": 90.0,
                "predicted_ceiling": 110.0,
                "realized_floor": 95.0,
                "realized_ceiling": 105.0,
                "abs_error_floor": 5.0,
                "abs_error_ceiling": 5.0,
            },
            {
                "prediction_key": "resolved-v1",
                "symbol": "AAA",
                "horizon": "d1",
                "predicted_as_of": "2026-08-24T20:00:00+00:00",
                "model_version": "v1",
                "predicted_floor": 98.0,
                "predicted_ceiling": 103.0,
                "realized_floor": 99.0,
                "realized_ceiling": 102.0,
                "abs_error_floor": 1.0,
                "abs_error_ceiling": 1.0,
            },
        ],
    )

    # Make one of the post-genesis predictions match the durable reconciliation key.
    from floor.prediction_reconciliation import prediction_key

    prediction_rows = [
        json.loads(line)
        for line in (data / "predictions" / "AAA.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    resolved_key = prediction_key(prediction_rows[0])
    reconciliation_path = data / "predictions" / "reconciliations" / "AAA.jsonl"
    reconciliations = [
        json.loads(line)
        for line in reconciliation_path.read_text(encoding="utf-8").splitlines()
    ]
    reconciliations[1]["prediction_key"] = resolved_key
    _write_jsonl(reconciliation_path, reconciliations)

    payload = build_experiment_observation(data)

    assert payload["status"] == "RUNNING"
    assert payload["league_id"] == "strategy_league_v1"
    assert payload["start_session"] == "2026-08-24"
    assert payload["safety"]["live_execution_enabled"] is False
    assert payload["safety"]["operational_paper_gateway_used"] is False

    d1 = next(row for row in payload["models"]["horizons"] if row["horizon"] == "d1")
    assert d1["metrics"]["resolved_predictions"] == 1
    assert d1["pending_predictions"] == 1
    assert d1["metrics"]["mean_abs_error_floor"] == 1.0
    assert d1["metrics"]["mean_abs_error_ceiling"] == 1.0
    assert d1["metrics"]["realized_range_coverage_rate"] == 1.0
    assert [item["model_version"] for item in d1["versions"]] == ["v1"]

    weekly = payload["models"]["weekly_opportunity_challenger"]
    assert weekly["status"] == "FROZEN"
    assert weekly["version"] == "league-v1"
    assert weekly["validation_metrics"]["spearman_rank_correlation"] == 0.25


def test_observation_history_is_once_per_league_session(tmp_path: Path) -> None:
    data = tmp_path / "data"
    league_path = data / "metrics" / "strategy_league" / "leaderboard.json"
    _write_json(league_path, _leaderboard())

    build_experiment_observation(data)
    build_experiment_observation(data)

    history = (
        data
        / "metrics"
        / "strategy_league"
        / "experiment_observation_history.jsonl"
    )
    assert len(history.read_text(encoding="utf-8").splitlines()) == 1

    _write_json(league_path, _leaderboard("2026-08-26", 3))
    build_experiment_observation(data)
    assert len(history.read_text(encoding="utf-8").splitlines()) == 2


def test_observation_history_keeps_epochs_distinct(tmp_path: Path) -> None:
    data = tmp_path / "data"
    league_path = data / "metrics" / "strategy_league" / "leaderboard.json"
    history = (
        data
        / "metrics"
        / "strategy_league"
        / "experiment_observation_history.jsonl"
    )

    _write_json(league_path, _leaderboard())
    first = build_experiment_observation(data)

    _write_json(
        league_path,
        _leaderboard(
            "2026-09-08",
            1,
            league_id="strategy_league_v2",
            start_session="2026-09-08",
        ),
    )
    second = build_experiment_observation(data)

    rows = [json.loads(line) for line in history.read_text(encoding="utf-8").splitlines()]
    assert first["league_id"] == "strategy_league_v1"
    assert second["league_id"] == "strategy_league_v2"
    assert second["start_session"] == "2026-09-08"
    assert [row["league_id"] for row in rows] == [
        "strategy_league_v1",
        "strategy_league_v2",
    ]



def _write_league_history(
    data: Path,
    league_id: str,
    sessions: list[tuple[str, str]],
) -> None:
    rows = [
        {
            "event": event,
            "state_after": {
                "league_id": league_id,
                "last_session": session,
            },
        }
        for event, session in sessions
    ]
    _write_jsonl(
        data
        / "metrics"
        / "strategy_league"
        / "runs"
        / league_id
        / "history.jsonl",
        rows,
    )


def test_observation_marks_missing_market_session_as_evidence_gap(tmp_path: Path) -> None:
    data = tmp_path / "data"
    league_id = "strategy_league_gap_test"
    league_path = data / "metrics" / "strategy_league" / "leaderboard.json"
    _write_json(
        league_path,
        _leaderboard(
            "2026-09-28",
            2,
            league_id=league_id,
            start_session="2026-09-24",
        ),
    )
    _write_league_history(
        data,
        league_id,
        [
            ("GENESIS", "2026-09-24"),
            ("EOD", "2026-09-28"),
        ],
    )

    payload = build_experiment_observation(data)
    continuity = payload["evidence"]["session_continuity"]

    assert continuity["status"] == "GAP_DETECTED"
    assert continuity["expected_sessions"] == 3
    assert continuity["observed_sessions"] == 2
    assert continuity["reported_sessions"] == 2
    assert continuity["missing_sessions"] == ["2026-09-25"]
    assert continuity["count_consistent"] is True


def test_observation_confirms_contiguous_prospective_sessions(tmp_path: Path) -> None:
    data = tmp_path / "data"
    league_id = "strategy_league_contiguous_test"
    league_path = data / "metrics" / "strategy_league" / "leaderboard.json"
    _write_json(
        league_path,
        _leaderboard(
            "2026-09-28",
            3,
            league_id=league_id,
            start_session="2026-09-24",
        ),
    )
    _write_league_history(
        data,
        league_id,
        [
            ("GENESIS", "2026-09-24"),
            ("EOD", "2026-09-25"),
            ("EOD", "2026-09-28"),
        ],
    )

    payload = build_experiment_observation(data)
    continuity = payload["evidence"]["session_continuity"]

    assert continuity["status"] == "OK"
    assert continuity["expected_sessions"] == 3
    assert continuity["observed_sessions"] == 3
    assert continuity["reported_sessions"] == 3
    assert continuity["missing_sessions"] == []
    assert continuity["unexpected_sessions"] == []
    assert continuity["count_consistent"] is True
