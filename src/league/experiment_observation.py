from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean, median
from typing import Any

from floor.prediction_reconciliation import prediction_key
from floor.storage import load_jsonl_rows

HORIZON_SESSIONS = {"d1": 1, "w1": 5, "q1": 10, "m3": 65}


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _finite(value: object) -> float | None:
    if not isinstance(value, (int, float, str, bytes, bytearray)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _date_prefix(value: object) -> str:
    return str(value or "").strip()[:10]


def _after_start(value: object, start_session: str | None) -> bool:
    if not start_session:
        return True
    day = _date_prefix(value)
    return bool(day and day >= start_session)


def _load_reconciliations(data_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    root = data_dir / "predictions" / "reconciliations"
    if not root.exists():
        return rows
    for path in sorted(root.glob("*.jsonl")):
        for row in load_jsonl_rows(path):
            if isinstance(row, dict):
                rows.append({str(key): value for key, value in row.items()})
    return rows


def _load_predictions(data_dir: Path) -> list[dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    root = data_dir / "predictions"
    if not root.exists():
        return []
    for path in sorted(root.glob("*.jsonl")):
        for row in load_jsonl_rows(path):
            if not isinstance(row, dict):
                continue
            item = {str(key): value for key, value in row.items()}
            horizon = str(item.get("horizon") or "").lower()
            if horizon not in HORIZON_SESSIONS:
                continue
            key = str(item.get("prediction_key") or "").strip() or prediction_key(item)
            item["prediction_key"] = key
            by_key.setdefault(key, item)
    return list(by_key.values())


def _mean(values: list[float]) -> float | None:
    return fmean(values) if values else None


def _median(values: list[float]) -> float | None:
    return median(values) if values else None


def _round(value: float | None, digits: int = 6) -> float | None:
    return round(value, digits) if value is not None else None


def _model_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    floor_errors: list[float] = []
    ceiling_errors: list[float] = []
    floor_pct: list[float] = []
    ceiling_pct: list[float] = []
    range_hits: list[float] = []
    m3_hits: list[float] = []

    for row in rows:
        floor_error = _finite(row.get("abs_error_floor"))
        ceiling_error = _finite(row.get("abs_error_ceiling"))
        realized_floor = _finite(row.get("realized_floor"))
        realized_ceiling = _finite(row.get("realized_ceiling"))
        predicted_floor = _finite(row.get("predicted_floor"))
        predicted_ceiling = _finite(row.get("predicted_ceiling"))

        if floor_error is not None:
            floor_errors.append(floor_error)
            if realized_floor not in (None, 0.0):
                floor_pct.append(abs(floor_error / realized_floor) * 100.0)
        if ceiling_error is not None:
            ceiling_errors.append(ceiling_error)
            if realized_ceiling not in (None, 0.0):
                ceiling_pct.append(abs(ceiling_error / realized_ceiling) * 100.0)
        if None not in (
            predicted_floor,
            predicted_ceiling,
            realized_floor,
            realized_ceiling,
        ):
            assert predicted_floor is not None
            assert predicted_ceiling is not None
            assert realized_floor is not None
            assert realized_ceiling is not None
            range_hits.append(
                1.0
                if predicted_floor <= realized_floor
                and predicted_ceiling >= realized_ceiling
                else 0.0
            )
        if isinstance(row.get("m3_week_hit"), bool):
            m3_hits.append(1.0 if row["m3_week_hit"] else 0.0)

    return {
        "resolved_predictions": len(rows),
        "unique_symbols": len({str(row.get("symbol") or "") for row in rows}),
        "mean_abs_error_floor": _round(_mean(floor_errors)),
        "median_abs_error_floor": _round(_median(floor_errors)),
        "mean_abs_error_ceiling": _round(_mean(ceiling_errors)),
        "median_abs_error_ceiling": _round(_median(ceiling_errors)),
        "mean_abs_error_floor_pct": _round(_mean(floor_pct), 4),
        "mean_abs_error_ceiling_pct": _round(_mean(ceiling_pct), 4),
        "realized_range_coverage_rate": _round(_mean(range_hits), 4),
        "m3_week_hit_rate": _round(_mean(m3_hits), 4),
    }


def _horizon_summary(
    horizon: str,
    reconciliations: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
    resolved_keys: set[str],
) -> dict[str, Any]:
    rows = [
        row
        for row in reconciliations
        if str(row.get("horizon") or "").lower() == horizon
    ]
    pending = [
        row
        for row in predictions
        if str(row.get("horizon") or "").lower() == horizon
        and str(row.get("prediction_key") or "") not in resolved_keys
    ]
    versions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        versions[str(row.get("model_version") or "unknown")].append(row)

    return {
        "horizon": horizon,
        "required_market_sessions": HORIZON_SESSIONS[horizon],
        "evidence_status": "RESOLVED" if rows else "WAITING_FOR_HORIZON",
        "pending_predictions": len(pending),
        "metrics": _model_metrics(rows),
        "versions": [
            {
                "model_version": version,
                "metrics": _model_metrics(version_rows),
            }
            for version, version_rows in sorted(versions.items())
        ],
    }


def _weekly_challenger(data_dir: Path) -> dict[str, Any]:
    artifact = _load_json(
        data_dir
        / "metrics"
        / "strategy_league"
        / "models"
        / "weekly_opportunity_challenger.json"
    )
    if not artifact:
        return {"status": "WAITING", "version": None, "validation_metrics": {}}
    return {
        "status": "FROZEN",
        "version": artifact.get("version"),
        "model_name": artifact.get("model_name"),
        "validation_metrics": artifact.get("metrics", {}),
        "automatic_promotion": False,
    }


def _append_history_once(path: Path, payload: dict[str, Any]) -> None:
    session = str(payload.get("last_session") or "")
    if not session:
        return
    if path.exists():
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if lines:
            try:
                last = json.loads(lines[-1])
            except json.JSONDecodeError:
                last = {}
            if str(last.get("last_session") or "") == session:
                return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def build_experiment_observation(data_dir: Path) -> dict[str, Any]:
    league_root = data_dir / "metrics" / "strategy_league"
    leaderboard = _load_json(league_root / "leaderboard.json")
    start_session_raw = str(leaderboard.get("start_session") or "").strip()
    start_session = start_session_raw or None
    last_session = str(leaderboard.get("last_session") or "").strip() or None

    reconciliations = [
        row
        for row in _load_reconciliations(data_dir)
        if _after_start(row.get("predicted_as_of"), start_session)
    ]
    predictions = [
        row
        for row in _load_predictions(data_dir)
        if _after_start(row.get("as_of"), start_session)
    ]
    resolved_keys = {
        str(row.get("prediction_key") or "")
        for row in reconciliations
        if str(row.get("prediction_key") or "")
    }

    payload: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "RUNNING" if start_session else "WAITING_FOR_GENESIS",
        "start_session": start_session,
        "last_session": last_session,
        "sessions": int(leaderboard.get("sessions", 0) or 0),
        "strategy_league": {
            "status": leaderboard.get("status", "WAITING"),
            "initial_nav_usd": leaderboard.get("initial_nav_usd", 100000.0),
            "rows": leaderboard.get("rows", []),
            "automatic_promotion": False,
            "live_execution_enabled": False,
        },
        "models": {
            "scope": "predictions created on or after Strategy League genesis",
            "horizons": [
                _horizon_summary(
                    horizon,
                    reconciliations,
                    predictions,
                    resolved_keys,
                )
                for horizon in HORIZON_SESSIONS
            ],
            "weekly_opportunity_challenger": _weekly_challenger(data_dir),
        },
        "evidence": {
            "prediction_count_since_genesis": len(predictions),
            "reconciled_count_since_genesis": len(reconciliations),
            "note": (
                "Prospective observational evidence only. Counts across symbols and dates "
                "are not assumed independent and are not a statistical significance claim."
            ),
        },
        "safety": {
            "operational_paper_gateway_used": False,
            "live_execution_enabled": False,
            "automatic_promotion": False,
        },
    }

    league_root.mkdir(parents=True, exist_ok=True)
    current_path = league_root / "experiment_observation.json"
    current_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _append_history_once(league_root / "experiment_observation_history.jsonl", payload)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the prospective Strategy League and model observation report"
    )
    parser.add_argument("--data-dir", default="data")
    args = parser.parse_args()
    payload = build_experiment_observation(Path(args.data_dir))
    print(
        json.dumps(
            {
                "status": payload.get("status"),
                "start_session": payload.get("start_session"),
                "last_session": payload.get("last_session"),
                "sessions": payload.get("sessions"),
                "reconciled": payload.get("evidence", {}).get(
                    "reconciled_count_since_genesis"
                ),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
