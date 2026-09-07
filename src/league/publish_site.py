from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BENCHMARK_IDS = {"benchmark_spy", "benchmark_equal_weight"}
CHALLENGER_ID = "capital_allocation_challenger"
DEFAULT_LEAGUE_ID = "strategy_league_v6_all_strategies_10k"


def _load_object(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_object(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _number(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric


def _return_sort_key(row: dict[str, Any]) -> tuple[bool, float]:
    value = _number(row.get("return"))
    return (
        value is not None,
        value if value is not None else float("-inf"),
    )


def _rank_rows(raw_rows: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_rows, list):
        return []
    rows = [dict(row) for row in raw_rows if isinstance(row, dict)]
    rows.sort(key=_return_sort_key, reverse=True)
    for index, row in enumerate(rows, start=1):
        row["rank"] = index
        strategy_id = str(row.get("strategy") or "")
        row["member_type"] = (
            "benchmark" if strategy_id in BENCHMARK_IDS else "strategy"
        )
    return rows


def _row_by_id(rows: list[dict[str, Any]], strategy_id: str) -> dict[str, Any] | None:
    return next(
        (row for row in rows if str(row.get("strategy")) == strategy_id),
        None,
    )


def _return_of(row: dict[str, Any] | None) -> float | None:
    return _number(row.get("return")) if row else None


def _delta(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return left - right


def _competition_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    strategy_rows = [row for row in rows if row.get("member_type") == "strategy"]
    base_rows = [
        row
        for row in strategy_rows
        if str(row.get("strategy")) != CHALLENGER_ID
    ]

    overall_leader = rows[0] if rows else None
    strategy_leader = strategy_rows[0] if strategy_rows else None
    challenger = _row_by_id(rows, CHALLENGER_ID)
    spy = _row_by_id(rows, "benchmark_spy")
    best_base = base_rows[0] if base_rows else None

    challenger_return = _return_of(challenger)
    return {
        "overall_leader": overall_leader.get("strategy") if overall_leader else None,
        "strategy_leader": strategy_leader.get("strategy") if strategy_leader else None,
        "strategy_leader_return": _return_of(strategy_leader),
        "challenger_rank": challenger.get("rank") if challenger else None,
        "challenger_return": challenger_return,
        "challenger_vs_spy": _delta(challenger_return, _return_of(spy)),
        "best_base_strategy": best_base.get("strategy") if best_base else None,
        "challenger_vs_best_base": _delta(
            challenger_return,
            _return_of(best_base),
        ),
        "members": len(rows),
        "strategies": len(strategy_rows),
        "benchmarks": len(rows) - len(strategy_rows),
    }


def _member_ids(league_cfg: dict[str, Any]) -> list[str]:
    raw = league_cfg.get("members", [])
    if not isinstance(raw, list):
        return []
    return [
        str(member.get("id"))
        for member in raw
        if isinstance(member, dict) and member.get("id")
    ]


def _waiting_payload(
    league_cfg: dict[str, Any],
    *,
    status: str,
    detail: str,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "league_id": str(league_cfg.get("league_id") or DEFAULT_LEAGUE_ID),
        "mode": "shadow_paper",
        "status": status,
        "detail": detail,
        "start_session": None,
        "last_session": None,
        "sessions": 0,
        "initial_nav_usd": float(league_cfg.get("initial_nav_usd", 10000.0)),
        "scheduled_members": _member_ids(league_cfg),
        "automatic_promotion": False,
        "live_execution_enabled": False,
        "rows": [],
    }


def publish_league_payload(
    data_dir: Path,
    output_path: Path,
    league_config_path: Path | None = None,
) -> dict[str, Any]:
    source = data_dir / "metrics" / "strategy_league" / "leaderboard.json"
    source_payload = _load_object(source)
    league_cfg = _load_object(league_config_path)
    expected_league_id = str(league_cfg.get("league_id") or DEFAULT_LEAGUE_ID)

    if not source_payload:
        payload = _waiting_payload(
            league_cfg,
            status="WAITING_FOR_WEEKLY_MODEL",
            detail="The prospective league has not started yet.",
        )
    elif league_cfg and str(source_payload.get("league_id") or "") != expected_league_id:
        previous_id = str(source_payload.get("league_id") or "unknown")
        payload = _waiting_payload(
            league_cfg,
            status="WAITING_FOR_GENESIS",
            detail=(
                f"Current runtime evidence belongs to previous league {previous_id}; "
                f"waiting for first complete EOD of {expected_league_id}."
            ),
        )
    else:
        payload = dict(source_payload)

    rows = _rank_rows(payload.get("rows", []))
    payload["rows"] = rows
    payload["summary"] = _competition_summary(rows)
    payload["evidence_type"] = "prospective_shadow_paper"
    payload["published_at"] = datetime.now(timezone.utc).isoformat()
    payload["automatic_promotion"] = False
    payload["live_execution_enabled"] = False
    _write_object(output_path, payload)
    return payload


def publish_observation_payload(
    data_dir: Path,
    output_path: Path,
) -> dict[str, Any]:
    source = (
        data_dir
        / "metrics"
        / "strategy_league"
        / "experiment_observation.json"
    )
    payload = _load_object(source)
    if not payload:
        payload = {
            "schema_version": 1,
            "status": "WAITING_FOR_GENESIS",
            "start_session": None,
            "last_session": None,
            "sessions": 0,
            "strategy_league": {
                "status": "WAITING",
                "rows": [],
                "automatic_promotion": False,
                "live_execution_enabled": False,
            },
            "models": {
                "scope": "predictions created on or after Strategy League genesis",
                "horizons": [],
                "weekly_opportunity_challenger": {
                    "status": "WAITING",
                    "version": None,
                    "validation_metrics": {},
                },
            },
            "evidence": {
                "prediction_count_since_genesis": 0,
                "reconciled_count_since_genesis": 0,
            },
            "safety": {
                "operational_paper_gateway_used": False,
                "live_execution_enabled": False,
                "automatic_promotion": False,
            },
        }
    payload.setdefault("safety", {})
    payload["safety"]["operational_paper_gateway_used"] = False
    payload["safety"]["live_execution_enabled"] = False
    payload["safety"]["automatic_promotion"] = False
    _write_object(output_path, payload)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Publish safe Strategy League data to GitHub Pages"
    )
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--output", default="site/data/strategy_league.json")
    parser.add_argument("--league-config", default="config/strategy_league.json")
    parser.add_argument(
        "--observation-output",
        default="site/data/experiment_observation.json",
    )
    args = parser.parse_args()
    data_dir = Path(args.data_dir)
    league_config = Path(args.league_config)
    output_path = Path(args.output)
    payload = publish_league_payload(
        data_dir,
        output_path,
        league_config,
    )
    observation = publish_observation_payload(
        data_dir,
        Path(args.observation_output),
    )

    # Research evidence is published beside the league payload, but remains
    # explicitly labeled retrospective/model-OOS/prospective as appropriate.
    from replay.publish_research_site import publish_research_payloads

    research = publish_research_payloads(
        data_dir=data_dir,
        site_data_dir=output_path.parent,
        league_config_path=league_config,
    )
    print(
        json.dumps(
            {
                "status": payload.get("status"),
                "sessions": payload.get("sessions"),
                "leader": (payload.get("summary") or {}).get("strategy_leader"),
                "observation_status": observation.get("status"),
                **research,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
