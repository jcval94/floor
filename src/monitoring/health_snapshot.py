from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from floor.universe import parse_universe_yaml
from utils.market_session import checkpoint_times, get_session_info
from utils.prediction_batch_guard import inspect_latest_prediction_batch

ET = ZoneInfo("America/New_York")
_STATUS_RANK = {"OK": 0, "DEGRADED": 1, "CRITICAL": 2}


def _parse_dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _check(name: str, status: str, detail: str) -> dict[str, str]:
    return {"name": name, "status": status, "detail": detail}


def _max_status(checks: list[dict[str, str]]) -> str:
    return max(
        (item["status"] for item in checks),
        key=lambda status: _STATUS_RANK[status],
        default="CRITICAL",
    )


def _dashboard_checks(data_dir: Path, now_utc: datetime) -> list[dict[str, str]]:
    path = data_dir / "reports" / "dashboard.json"
    payload = _read_json(path)
    if payload is None:
        return [
            _check("dashboard_present", "CRITICAL", "dashboard.json missing or invalid"),
            _check("prediction_recency", "CRITICAL", "latest predictions unavailable"),
        ]

    checks = [_check("dashboard_present", "OK", "dashboard.json loaded")]
    generated_at = _parse_dt(payload.get("generated_at") or payload.get("as_of"))
    if generated_at is None:
        checks.append(_check("dashboard_recency", "CRITICAL", "dashboard timestamp missing or invalid"))
    else:
        age = now_utc - generated_at
        if age < timedelta(minutes=-5):
            checks.append(_check("dashboard_recency", "CRITICAL", "dashboard timestamp is in the future"))
        elif age > timedelta(hours=96):
            checks.append(_check("dashboard_recency", "CRITICAL", "dashboard older than 96h"))
        elif age > timedelta(hours=24):
            checks.append(_check("dashboard_recency", "DEGRADED", "dashboard older than 24h"))
        else:
            checks.append(_check("dashboard_recency", "OK", "dashboard age <= 24h"))

    predictions = payload.get("latest_predictions")
    if not isinstance(predictions, list) or not predictions:
        checks.append(_check("prediction_recency", "CRITICAL", "latest_predictions is empty"))
        return checks

    timestamps = [
        _parse_dt(item.get("as_of"))
        for item in predictions
        if isinstance(item, dict)
    ]
    valid = [value for value in timestamps if value is not None]
    if not valid:
        checks.append(_check("prediction_recency", "CRITICAL", "prediction timestamps missing or invalid"))
        return checks

    age = now_utc - max(valid)
    if age < timedelta(minutes=-5):
        checks.append(_check("prediction_recency", "CRITICAL", "latest prediction timestamp is in the future"))
    elif age > timedelta(hours=96):
        checks.append(_check("prediction_recency", "CRITICAL", "latest prediction older than 96h"))
    elif age > timedelta(hours=24):
        checks.append(_check("prediction_recency", "DEGRADED", "latest prediction older than 24h"))
    else:
        checks.append(_check("prediction_recency", "OK", "latest prediction age <= 24h"))
    return checks


def _prediction_batch_check(data_dir: Path, universe_path: Path | None) -> dict[str, str]:
    if universe_path is None:
        return _check("prediction_batch_completeness", "OK", "universe check not configured")
    if not universe_path.exists():
        return _check("prediction_batch_completeness", "CRITICAL", f"universe file missing: {universe_path}")
    symbols = parse_universe_yaml(universe_path)
    try:
        result = inspect_latest_prediction_batch(data_dir, symbols)
    except RuntimeError as exc:
        return _check("prediction_batch_completeness", "CRITICAL", str(exc))
    if not result.get("complete"):
        missing = result.get("missing", [])
        duplicates = result.get("duplicates", [])
        return _check(
            "prediction_batch_completeness",
            "CRITICAL",
            "latest prediction batch incomplete "
            f"event={result.get('event_type')} missing={missing[:12] if isinstance(missing, list) else missing} "
            f"duplicates={duplicates[:12] if isinstance(duplicates, list) else duplicates}",
        )
    return _check(
        "prediction_batch_completeness",
        "OK",
        f"latest batch complete event={result.get('event_type')} pairs={result.get('observed_pairs')}/{result.get('expected_pairs')}",
    )


def _retraining_check(data_dir: Path) -> dict[str, str]:
    payload = _read_json(data_dir / "training" / "review_summary_latest.json")
    if payload is None:
        return _check("retraining_review", "DEGRADED", "retraining review missing or invalid")
    suite_status = str(payload.get("suite_status") or "UNKNOWN").upper()
    recommendation = str(payload.get("suite_recommendation") or "UNKNOWN").upper()
    if suite_status in {"ALERT", "CRITICAL", "RED"} or recommendation == "RETRAIN_NOW":
        return _check(
            "retraining_review",
            "DEGRADED",
            f"review requires attention: status={suite_status} recommendation={recommendation}",
        )
    if suite_status in {"WARN", "WARNING", "YELLOW"} or recommendation == "RETRAIN_SOON":
        return _check(
            "retraining_review",
            "DEGRADED",
            f"review warning: status={suite_status} recommendation={recommendation}",
        )
    return _check(
        "retraining_review",
        "OK",
        f"review healthy: status={suite_status} recommendation={recommendation}",
    )


def _checkpoint_check(data_dir: Path, now_et: datetime) -> dict[str, str]:
    info = get_session_info(now_et)
    if not info.is_open_day:
        return _check("checkpoint_completeness", "OK", "market closed today; no checkpoints required")
    checkpoints = checkpoint_times(info)
    grace = timedelta(minutes=45)
    expected = [event for event, timestamp in checkpoints.items() if now_et >= timestamp + grace]
    if not expected:
        return _check("checkpoint_completeness", "OK", "no checkpoint is past its grace window")
    day = info.session_day.isoformat()
    marker_dir = data_dir / "snapshots" / "workflow_runs"
    missing = [event for event in expected if not (marker_dir / f"intraday_{day}_{event}.json").exists()]
    if not missing:
        return _check("checkpoint_completeness", "OK", "all elapsed checkpoints are marked complete")
    after_close = bool(info.market_close and now_et >= info.market_close + timedelta(minutes=60))
    status = "CRITICAL" if after_close else "DEGRADED"
    return _check("checkpoint_completeness", status, f"missing elapsed checkpoints: {','.join(missing)}")


def build_health_snapshot(
    data_dir: Path,
    now: datetime | None = None,
    universe_path: Path | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(tz=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now_utc = now.astimezone(timezone.utc)
    now_et = now.astimezone(ET)

    checks = _dashboard_checks(data_dir, now_utc)
    checks.append(_prediction_batch_check(data_dir, universe_path))
    checks.append(_retraining_check(data_dir))
    checks.append(_checkpoint_check(data_dir, now_et))
    status = _max_status(checks)
    alerts = [item["detail"] for item in checks if item["status"] != "OK"]
    return {
        "generated_at": now_utc.isoformat(),
        "status": status,
        "series": checks,
        "alerts": alerts,
    }


def _semantic_state(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": payload.get("status"),
        "series": payload.get("series"),
        "alerts": payload.get("alerts"),
    }


def write_health_snapshot(
    data_dir: Path,
    output_path: Path,
    now: datetime | None = None,
    universe_path: Path | None = None,
) -> dict[str, Any]:
    payload = build_health_snapshot(data_dir, now=now, universe_path=universe_path)
    previous = _read_json(output_path)
    if previous is not None and _semantic_state(previous) == _semantic_state(payload):
        previous_generated_at = previous.get("generated_at")
        if isinstance(previous_generated_at, str) and previous_generated_at:
            payload["generated_at"] = previous_generated_at
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a fail-closed operational health snapshot")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--output", default="data/metrics/public_metrics.json")
    parser.add_argument("--universe", default="config/universe.yaml")
    args = parser.parse_args()
    payload = write_health_snapshot(
        Path(args.data_dir),
        Path(args.output),
        universe_path=Path(args.universe),
    )
    print(json.dumps(payload, ensure_ascii=False))
    return 1 if payload["status"] == "CRITICAL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
