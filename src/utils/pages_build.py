from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import shutil
from pathlib import Path
from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from typing import Any

from floor.calendar import is_market_session
from floor.reporting.generate_site_data import build_dashboard_snapshot
from floor.schemas import MULTI_HORIZON_PREDICTION_CONTRACT
from floor.universe import parse_universe_yaml

ALLOWED_KEYS = {
    "prediction_files",
    "signal_files",
    "latest_predictions",
    "generated_at",
    "system_health",
    "prediction_contract",
}

SENSITIVE_KEYS = {
    "api_key",
    "secret",
    "token",
    "password",
    "authorization",
}

LFS_POINTER_HEADER = "version https://git-lfs.github.com/spec/v1"


def _sanitize(obj: Any) -> Any:
    if isinstance(obj, dict):
        clean = {}
        for k, v in obj.items():
            lk = k.lower()
            if any(s in lk for s in SENSITIVE_KEYS):
                continue
            clean[k] = _sanitize(v)
        return clean
    if isinstance(obj, list):
        return [_sanitize(x) for x in obj]
    return obj


def _is_lfs_pointer(content: str) -> bool:
    return content.startswith(LFS_POINTER_HEADER)


def _read_json(path: Path, default: Any) -> Any:
    if not path or not path.exists():
        return default
    content = path.read_text(encoding="utf-8").strip()
    if not content or _is_lfs_pointer(content):
        return default
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return default


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    content = path.read_text(encoding="utf-8")
    if _is_lfs_pointer(content):
        return out
    for line in content.splitlines():
        line = line.strip()
        if line:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                out.append(payload)
    return out


def _latest_mtime(paths: list[Path]) -> float | None:
    mtimes = [path.stat().st_mtime for path in paths if path.exists()]
    return max(mtimes) if mtimes else None


def _refresh_dashboard_snapshot_if_stale(data_dir: Path, dashboard_path: Path) -> None:
    prediction_dir = data_dir / "predictions"
    signal_dir = data_dir / "signals"
    sqlite_path = data_dir / "persistence" / "app.sqlite"
    refresh_inputs = [
        *prediction_dir.glob("*.jsonl"),
        *signal_dir.glob("*.jsonl"),
        sqlite_path,
    ]
    latest_input_mtime = _latest_mtime(refresh_inputs)
    dashboard_mtime = dashboard_path.stat().st_mtime if dashboard_path.exists() else None
    if latest_input_mtime is None:
        return
    if dashboard_mtime is not None and dashboard_mtime >= latest_input_mtime:
        return
    build_dashboard_snapshot(data_dir, output_path=dashboard_path)


def _latest_report_file(reports_dir: Path, pattern: str) -> Path | None:
    candidates = list(reports_dir.glob(pattern))
    if not candidates:
        return None

    date_re = re.compile(r"(\d{4}-\d{2}-\d{2})")

    def _sort_key(path: Path) -> tuple[date, float]:
        match = date_re.search(path.stem)
        parsed_date = date.min
        if match:
            try:
                parsed_date = date.fromisoformat(match.group(1))
            except ValueError:
                parsed_date = date.min
        return (parsed_date, path.stat().st_mtime)

    return max(candidates, key=_sort_key)


def _review_drift_payload(review_summary: Any) -> dict[str, Any] | None:
    if not isinstance(review_summary, dict):
        return None
    source_date = review_summary.get("as_of")
    models = review_summary.get("models")
    if not source_date or not isinstance(models, dict) or not models:
        return None

    rank = {"GREEN": 0, "YELLOW": 1, "RED": 2}
    worst = "GREEN"
    thresholds: list[dict[str, Any]] = []
    metrics: dict[str, Any] = {}

    for model_key, raw_model in sorted(models.items()):
        if not isinstance(raw_model, dict):
            continue
        level = str(raw_model.get("drift_level") or "GREEN").upper()
        if rank.get(level, 0) > rank.get(worst, 0):
            worst = level
        summary = raw_model.get("summary")
        summary = summary if isinstance(summary, dict) else {}
        component_metrics: dict[str, Any] = {}
        for component in ("shared_data", "schema", "target", "performance"):
            raw_component = summary.get(component)
            if not isinstance(raw_component, dict):
                continue
            state = str(raw_component.get("state") or "UNKNOWN").upper()
            score = raw_component.get("score")
            component_metrics[component] = {"state": state, "score": score}
            if state not in {"", "GREEN", "OK"}:
                thresholds.append(
                    {
                        "name": f"{model_key}.{component}",
                        "observed": score,
                        "threshold": "GREEN required",
                        "severity": state,
                    }
                )
        metrics[str(model_key)] = component_metrics

    return {
        "status": review_summary.get("suite_status", "UNKNOWN"),
        "decision": review_summary.get("suite_recommendation", "PENDING"),
        "drift_level": worst,
        "thresholds": thresholds,
        "metrics": metrics,
        "source_file": "training/review_summary_latest.json",
        "source_date": source_date,
        "source_kind": "governed_training_review",
    }


def _operational_incident_payload(metrics_payload: Any) -> dict[str, Any] | None:
    if not isinstance(metrics_payload, dict):
        return None
    source_date = metrics_payload.get("generated_at")
    reported = str(metrics_payload.get("status") or "").upper()
    if not source_date or reported not in {"OK", "DEGRADED", "CRITICAL"}:
        return None

    alerts = metrics_payload.get("alerts")
    alerts = [str(item) for item in alerts] if isinstance(alerts, list) else []
    series = metrics_payload.get("series")
    series = series if isinstance(series, list) else []
    non_ok = [
        item
        for item in series
        if isinstance(item, dict)
        and str(item.get("status") or "UNKNOWN").upper() != "OK"
    ]

    if reported == "CRITICAL":
        status, severity = "ESCALATE", "SEV2"
        symptom = "; ".join(alerts) or "Operational health is CRITICAL."
    elif reported == "DEGRADED":
        status, severity = "MONITORING", "SEV3"
        symptom = "; ".join(alerts) or "Operational health is DEGRADED."
    else:
        status, severity = "OK", "SEV4"
        symptom = "No active operational incidents."

    impact = {
        str(item.get("name") or f"check_{idx}"): str(item.get("status") or "UNKNOWN")
        for idx, item in enumerate(non_ok)
    }
    return {
        "status": status,
        "severity": severity,
        "summary": {
            "symptom": symptom,
            "operational_health": reported,
        },
        "impact": impact,
        "alerts": alerts,
        "source_file": "metrics/public_metrics.json",
        "source_date": source_date,
        "source_kind": "operational_health_snapshot",
    }


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _max_source_timestamp(source_values: dict[str, dict[str, Any]]) -> str | None:
    best: tuple[datetime, str] | None = None
    for payload in source_values.values():
        if not isinstance(payload, dict):
            continue
        ts_value = payload.get("as_of") or payload.get("timestamp") or payload.get("fetched_at")
        ts_raw = str(ts_value).strip() if ts_value is not None else ""
        if not ts_raw:
            continue
        parsed = _parse_iso_datetime(ts_raw)
        if parsed is None:
            continue
        if best is None or parsed > best[0]:
            best = (parsed, ts_raw)
    return best[1] if best else None


def _compute_coverage(total_symbols: int, observed_symbols: int) -> float:
    if total_symbols <= 0:
        return 0.0
    return observed_symbols / float(total_symbols)


def _market_session_date_after(start: date, sessions: int) -> date:
    cursor = start
    remaining = max(int(sessions), 0)
    while remaining:
        cursor += timedelta(days=1)
        if is_market_session(cursor):
            remaining -= 1
    return cursor


def _compute_retraining_schedule(
    last_review_at: Any,
    cadence_days: int,
    recommendation: str = "",
    retrain_soon_sessions: int = 3,
) -> dict[str, Any]:
    cadence_days = max(int(cadence_days), 1)
    retrain_soon_sessions = max(int(retrain_soon_sessions), 1)
    recommendation = str(recommendation).upper()
    action_required_now = recommendation == "RETRAIN_NOW"
    advisory_active = recommendation == "RETRAIN_SOON"
    last_dt = _parse_iso_datetime(last_review_at)
    if last_dt is None:
        return {
            "cadence_days": cadence_days,
            "last_review_at": None,
            "next_review_at": None,
            "seconds_until_due": None,
            "recommended_retrain_by": None,
            "recommended_retrain_window_sessions": (
                retrain_soon_sessions if advisory_active else 0
            ),
            "retrain_advisory_active": advisory_active,
            "human_eta": (
                "reentrenamiento requerido ahora"
                if action_required_now
                else "sin fecha de última revisión"
            ),
            "is_overdue": None,
            "action_required_now": action_required_now,
        }

    now = datetime.now(tz=timezone.utc)
    next_dt = last_dt + timedelta(days=cadence_days)
    seconds_until_due = int((next_dt - now).total_seconds())
    is_overdue = seconds_until_due < 0
    abs_seconds = abs(seconds_until_due)
    days = abs_seconds // 86400
    hours = (abs_seconds % 86400) // 3600
    prefix = "vencido hace" if is_overdue else "faltan"

    recommended_retrain_by: str | None = None
    if advisory_active:
        recommended_retrain_by = _market_session_date_after(
            last_dt.date(),
            retrain_soon_sessions,
        ).isoformat()
        human_eta = (
            f"reentrenamiento recomendado antes de {recommended_retrain_by} "
            f"({retrain_soon_sessions} sesiones)"
        )
    elif action_required_now:
        recommended_retrain_by = last_dt.date().isoformat()
        human_eta = "reentrenamiento requerido ahora"
    else:
        human_eta = f"{prefix} {days}d {hours}h"

    return {
        "cadence_days": cadence_days,
        "last_review_at": last_dt.isoformat(),
        "next_review_at": next_dt.isoformat(),
        "seconds_until_due": seconds_until_due,
        "recommended_retrain_by": recommended_retrain_by,
        "recommended_retrain_window_sessions": (
            retrain_soon_sessions if advisory_active else 0
        ),
        "retrain_advisory_active": advisory_active,
        "human_eta": human_eta,
        "is_overdue": is_overdue,
        "action_required_now": action_required_now,
    }


def _build_model_detail(
    model_key: str,
    review_model: Any,
    artifact: Any,
    competition: Any = None,
) -> dict[str, Any]:
    review_model = review_model if isinstance(review_model, dict) else {}
    artifact = artifact if isinstance(artifact, dict) else {}
    competition = competition if isinstance(competition, dict) else {}
    summary = review_model.get("summary", {}) if isinstance(review_model.get("summary"), dict) else {}
    performance = summary.get("performance", {}) if isinstance(summary.get("performance"), dict) else {}
    shared_data = summary.get("shared_data", {}) if isinstance(summary.get("shared_data"), dict) else {}
    target = summary.get("target", {}) if isinstance(summary.get("target"), dict) else {}
    schema = summary.get("schema", {}) if isinstance(summary.get("schema"), dict) else {}
    has_review = bool(review_model)
    has_artifact = bool(artifact)
    monitoring_metrics = performance.get("current_metrics", {})
    if not isinstance(monitoring_metrics, dict):
        monitoring_metrics = {}
    validation_metrics = artifact.get("metrics", {})
    if not isinstance(validation_metrics, dict):
        validation_metrics = {}

    competition_benchmark = competition.get("central_benchmark", {})
    competition_benchmark = (
        competition_benchmark
        if isinstance(competition_benchmark, dict)
        else {}
    )
    active_skill = competition_benchmark.get("active_skill_vs_atr", {})
    active_skill = active_skill if isinstance(active_skill, dict) else {}
    stability = competition_benchmark.get("active_temporal_stability", {})
    stability = stability if isinstance(stability, dict) else {}
    atr_metrics = competition_benchmark.get("atr_only_metrics", {})
    atr_metrics = atr_metrics if isinstance(atr_metrics, dict) else {}

    artifact_params = artifact.get("params", {})
    artifact_params = artifact_params if isinstance(artifact_params, dict) else {}
    artifact_benchmark = artifact_params.get("central_benchmark", {})
    artifact_benchmark = (
        artifact_benchmark if isinstance(artifact_benchmark, dict) else {}
    )
    artifact_skill = artifact_benchmark.get("skill_vs_atr", {})
    artifact_skill = artifact_skill if isinstance(artifact_skill, dict) else {}
    artifact_stability = artifact_benchmark.get("temporal_stability", {})
    artifact_stability = (
        artifact_stability
        if isinstance(artifact_stability, dict)
        else {}
    )
    dummy = artifact_params.get("dummy_benchmark", {})
    dummy = dummy if isinstance(dummy, dict) else {}

    spread_skill = active_skill.get(
        "spread",
        validation_metrics.get(
            "central_skill_vs_atr",
            artifact_skill.get("spread"),
        ),
    )
    floor_skill = active_skill.get(
        "floor",
        validation_metrics.get(
            "central_skill_floor_vs_atr",
            artifact_skill.get("floor"),
        ),
    )
    ceiling_skill = active_skill.get(
        "ceiling",
        validation_metrics.get(
            "central_skill_ceiling_vs_atr",
            artifact_skill.get("ceiling"),
        ),
    )
    atr_spread = atr_metrics.get(
        "mae_spread_pct",
        dummy.get("atr_only_mae_spread_pct"),
    )
    model_spread = validation_metrics.get("mae_spread_pct")
    if atr_spread is not None and spread_skill is not None:
        try:
            model_spread = float(atr_spread) * (1.0 - float(spread_skill))
        except (TypeError, ValueError):
            pass
    central_benchmark = {
        "benchmark": "atr_only",
        "model_mae_spread_pct": model_spread,
        "atr_only_mae_spread_pct": atr_spread,
        "skill_vs_atr": spread_skill,
        "floor_skill_vs_atr": floor_skill,
        "ceiling_skill_vs_atr": ceiling_skill,
        "status": (
            "MODEL_SUPERIOR"
            if isinstance(spread_skill, (int, float)) and spread_skill > 0
            else "ATR_ONLY_SUPERIOR"
            if isinstance(spread_skill, (int, float))
            else "UNKNOWN"
        ),
        "temporal_stability": stability or artifact_stability,
        "test_used_for_selection": False,
        "coverage_used_for_selection": False,
    }

    return {
        "model_key": model_key,
        "model_name": review_model.get("model_name", artifact.get("model_name", "unknown")),
        "current_version": review_model.get("current_version", artifact.get("version", "unknown")),
        "status": review_model.get("status", "UNREVIEWED" if has_artifact else "UNKNOWN"),
        "drift_level": review_model.get("drift_level", "UNKNOWN" if not has_review else "GREEN"),
        "recommendation": review_model.get(
            "recommendation",
            review_model.get("action", "REVIEW_PENDING" if has_artifact else "PENDING"),
        ),
        "auto_retrain": bool(review_model.get("auto_retrain", False)),
        "as_of": review_model.get("as_of"),
        "reason": review_model.get("reason", ""),
        "metrics": {
            "current": monitoring_metrics,
            "baseline": performance.get("baseline_metrics", {}),
            "deltas": performance.get("deltas", {}),
        },
        "monitoring_metrics": monitoring_metrics,
        "validation_metrics": validation_metrics,
        "validation_source": "champion_artifact" if validation_metrics else None,
        "central_benchmark": central_benchmark,
        "drift_components": {
            "shared_data": {"state": shared_data.get("state"), "score": shared_data.get("score")},
            "target": {"state": target.get("state"), "score": target.get("score")},
            "schema": {"state": schema.get("state"), "score": schema.get("score")},
            "performance": {"state": performance.get("state"), "score": performance.get("score")},
        },
        "artifact": {
            "model_type": artifact.get("model_type"),
            "trained_at": artifact.get("trained_at") or artifact.get("as_of"),
            "train_rows": artifact.get("train_rows"),
            "test_rows": artifact.get("test_rows"),
            "dataset_summary": artifact.get("dataset_summary", {}),
            "params": artifact.get("params", {}),
        },
    }


def _build_m3_detail(value_detail: dict[str, Any], timing_detail: dict[str, Any]) -> dict[str, Any]:
    value_version = str(value_detail.get("current_version", "unknown"))
    timing_version = str(timing_detail.get("current_version", "unknown"))
    value_metrics = value_detail.get("metrics", {}).get("current", {}) if isinstance(value_detail.get("metrics"), dict) else {}
    timing_metrics = timing_detail.get("metrics", {}).get("current", {}) if isinstance(timing_detail.get("metrics"), dict) else {}
    monitoring_metrics = {
        "pinball_loss_m3": value_metrics.get("pinball_loss"),
        "mae_realized_floor_m3": value_metrics.get("mae_realized_floor"),
        "top1_accuracy_m3": timing_metrics.get("top1_accuracy"),
        "top3_accuracy_m3": timing_metrics.get("top3_accuracy"),
    }
    monitoring_metrics = {key: value for key, value in monitoring_metrics.items() if value is not None}
    value_validation = value_detail.get("validation_metrics", {})
    timing_validation = timing_detail.get("validation_metrics", {})
    validation_metrics = {
        "value": value_validation if isinstance(value_validation, dict) else {},
        "timing": timing_validation if isinstance(timing_validation, dict) else {},
    }
    value_status = str(value_detail.get("status") or "UNKNOWN")
    timing_status = str(timing_detail.get("status") or "UNKNOWN")
    combined_status = value_status if value_status == timing_status else (
        timing_status if value_status == "UNKNOWN" else value_status
    )

    return {
        "model_key": "m3",
        "model_name": "m3_value_linear + m3_timing_multiclass",
        "current_version": f"value:{value_version}|timing:{timing_version}",
        "status": combined_status,
        "drift_level": value_detail.get("drift_level", timing_detail.get("drift_level", "UNKNOWN")),
        "recommendation": value_detail.get("recommendation", timing_detail.get("recommendation", "PENDING")),
        "auto_retrain": bool(value_detail.get("auto_retrain", False) or timing_detail.get("auto_retrain", False)),
        "as_of": value_detail.get("as_of") or timing_detail.get("as_of"),
        "reason": value_detail.get("reason") or timing_detail.get("reason") or "",
        "metrics": {"current": monitoring_metrics, "baseline": {}, "deltas": {}},
        "monitoring_metrics": monitoring_metrics,
        "validation_metrics": validation_metrics,
        "validation_source": "champion_artifact" if any(validation_metrics.values()) else None,
        "drift_components": {
            "shared_data": {"state": None, "score": None},
            "target": {"state": None, "score": None},
            "schema": {"state": None, "score": None},
            "performance": {"state": None, "score": None},
        },
        "artifact": {
            "model_type": "ensemble",
            "trained_at": value_detail.get("artifact", {}).get("trained_at") or timing_detail.get("artifact", {}).get("trained_at"),
            "dataset_summary": value_detail.get("artifact", {}).get("dataset_summary", {}),
            "params": {
                "value": value_detail.get("artifact", {}).get("params", {}),
                "timing": timing_detail.get("artifact", {}).get("params", {}),
            },
        },
    }

def _champion_versions_from_artifacts(artifacts: dict[str, dict]) -> dict[str, dict]:
    champion_versions: dict[str, dict] = {}
    for task in ("d1", "w1", "q1", "value", "timing"):
        artifact = artifacts.get(task, {}) if isinstance(artifacts, dict) else {}
        champion_versions[task] = {
            "current_version": artifact.get("version", "unknown"),
            "model_name": artifact.get("model_name", "unknown"),
        }
    champion_versions["m3"] = {
        "current_version": (
            f"value:{champion_versions['value']['current_version']}|"
            f"timing:{champion_versions['timing']['current_version']}"
        ),
        "model_name": "m3_value_linear + m3_timing_multiclass",
    }
    return champion_versions


def _fallback_suite_version_from_artifacts(artifacts: dict[str, dict]) -> str:
    value = artifacts.get("value", {}) if isinstance(artifacts, dict) else {}
    timing = artifacts.get("timing", {}) if isinstance(artifacts, dict) else {}
    value_version = str(value.get("version") or "").strip()
    timing_version = str(timing.get("version") or "").strip()
    if not value_version and not timing_version:
        return ""
    value_name = value.get("model_name", "m3_value_linear")
    timing_name = timing.get("model_name", "m3_timing_multiclass")
    return f"value:{value_name}@{(value_version or 'unknown')}|timing:{timing_name}@{(timing_version or 'unknown')}"


def _review_summary_is_stale(review_models: dict[str, Any], artifacts: dict[str, dict]) -> bool:
    if not isinstance(review_models, dict):
        return False
    for task in ("d1", "w1", "q1", "value", "timing"):
        summary_model = review_models.get(task, {})
        if not isinstance(summary_model, dict):
            continue
        summary_version = str(summary_model.get("current_version") or "").strip()
        if not summary_version:
            continue
        artifact = artifacts.get(task, {}) if isinstance(artifacts, dict) else {}
        artifact_version = str(artifact.get("version") or "").strip()
        if artifact_version and summary_version != artifact_version:
            return True
    return False


def _latest_model_artifact_timestamp(artifacts: dict[str, dict]) -> str | None:
    latest: datetime | None = None
    for task in ("d1", "w1", "q1", "value", "timing"):
        artifact = artifacts.get(task, {}) if isinstance(artifacts, dict) else {}
        parsed = _parse_iso_datetime(artifact.get("trained_at") or artifact.get("as_of"))
        if parsed is None:
            continue
        if latest is None or parsed > latest:
            latest = parsed
    return latest.isoformat() if latest else None


def _read_review_int(config_path: Path, key: str, default: int) -> int:
    if not config_path.exists():
        return default
    content = config_path.read_text(encoding="utf-8", errors="ignore")
    match = re.search(
        rf"^\s*{re.escape(key)}\s*:\s*(\d+)\s*$",
        content,
        flags=re.MULTILINE,
    )
    if not match:
        return default
    try:
        return max(int(match.group(1)), 1)
    except ValueError:
        return default


def _read_cadence_days(config_path: Path, default: int = 14) -> int:
    return _read_review_int(config_path, "cadence_days", default)


def _read_retrain_soon_sessions(config_path: Path, default: int = 3) -> int:
    return _read_review_int(config_path, "retrain_soon_sessions", default)


def _opportunity_row(row: dict) -> dict | None:
    floor_raw = row.get("floor_value")
    ceiling_raw = row.get("ceiling_value")
    if floor_raw is None or ceiling_raw is None:
        return None

    try:
        floor = float(floor_raw)
        ceiling = float(ceiling_raw)
    except (TypeError, ValueError):
        return None
    spread_abs = max(ceiling - floor, 0.0)
    midpoint = (ceiling + floor) / 2.0
    spread_rel = spread_abs / max(abs(midpoint), 1e-6)

    try:
        floor_prob = float(row.get("floor_time_probability", 0.5) or 0.5)
    except (TypeError, ValueError):
        floor_prob = 0.5
    try:
        ceiling_prob = float(row.get("ceiling_time_probability", 0.5) or 0.5)
    except (TypeError, ValueError):
        ceiling_prob = 0.5
    confidence = max(min((floor_prob + ceiling_prob) / 2.0, 1.0), 0.0)

    score = spread_abs * spread_rel * confidence

    return {
        "symbol": row.get("symbol"),
        "horizon": row.get("horizon"),
        "floor": round(floor, 4),
        "ceiling": round(ceiling, 4),
        "spread": round(spread_abs, 4),
        "spread_relative": round(spread_rel, 6),
        "spread_relative_pct": round(spread_rel * 100.0, 2),
        "confidence": round(confidence, 4),
        "opportunity_score": round(score, 6),
        "event_type": row.get("event_type"),
        "as_of": row.get("as_of"),
    }


def _latest_market_values(db_path: Path, symbols: list[str]) -> dict[str, dict[str, Any]]:
    if not db_path.exists() or not symbols:
        return {}
    placeholders = ",".join("?" for _ in symbols)
    query = f"""
        SELECT d.symbol, d.ts_utc, d.close, d.source, d.fetched_at_utc
        FROM daily_bars d
        JOIN (
            SELECT symbol, MAX(ts_utc) AS max_ts
            FROM daily_bars
            WHERE symbol IN ({placeholders})
            GROUP BY symbol
        ) x ON x.symbol = d.symbol AND x.max_ts = d.ts_utc
        ORDER BY d.symbol ASC
    """
    try:
        with sqlite3.connect(db_path) as conn:
            has_table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='daily_bars' LIMIT 1"
            ).fetchone()
            if not has_table:
                return {}
            rows = conn.execute(query, [s.upper() for s in symbols]).fetchall()
    except sqlite3.Error:
        return {}

    return {
        str(row[0]).upper(): {
            "as_of": row[1],
            "close": float(row[2]),
            "source": row[3],
            "fetched_at": row[4],
        }
        for row in rows
    }


def _latest_close_from_rows(rows_path: Path, symbols: list[str]) -> dict[str, dict[str, Any]]:
    if not rows_path.exists() or not symbols:
        return {}
    allowed = {s.upper() for s in symbols}
    latest: dict[str, tuple[str, int, dict[str, Any]]] = {}
    for idx, row in enumerate(_read_jsonl(rows_path)):
        symbol = str(row.get("symbol", "")).upper()
        if symbol not in allowed:
            continue
        ts = str(row.get("timestamp") or row.get("as_of") or "")
        close = row.get("close")
        if not ts:
            continue
        if close is None:
            continue
        try:
            close_value = float(str(close))
        except (TypeError, ValueError):
            continue
        prev = latest.get(symbol)
        if prev is None or (ts, idx) >= (prev[0], prev[1]):
            latest[symbol] = (
                ts,
                idx,
                {
                    "as_of": ts,
                    "close": close_value,
                    "source": "training/yahoo_market_rows.jsonl",
                },
            )
    return {symbol: payload for symbol, (_, __, payload) in latest.items()}


def build_pages_data(data_dir: Path, site_data_dir: Path, universe_path: Path) -> None:
    site_data_dir.mkdir(parents=True, exist_ok=True)

    dashboard_src = data_dir / "reports" / "dashboard.json"
    _refresh_dashboard_snapshot_if_stale(data_dir, dashboard_src)
    dashboard_payload = {
        "prediction_files": 0,
        "signal_files": 0,
        "latest_predictions": [],
        "system_health": "UNKNOWN",
    }
    dashboard_payload.update(_read_json(dashboard_src, {}))

    dashboard_payload = {k: v for k, v in _sanitize(dashboard_payload).items() if k in ALLOWED_KEYS}
    (site_data_dir / "dashboard.json").write_text(json.dumps(dashboard_payload, indent=2), encoding="utf-8")

    metrics_payload = _sanitize(_read_json(data_dir / "metrics" / "public_metrics.json", {"status": "no_public_metrics", "series": []}))
    (site_data_dir / "metrics.json").write_text(json.dumps(metrics_payload, indent=2), encoding="utf-8")

    strategy_payload = _sanitize(_read_json(data_dir / "reports" / "strategy.json", {"status": "no_strategy_report", "equity_curve": []}))
    (site_data_dir / "strategy.json").write_text(json.dumps(strategy_payload, indent=2), encoding="utf-8")

    symbols = list(parse_universe_yaml(universe_path))
    universe = {
        "name": "us_top50_liquid_v1",
        "symbols": symbols,
    }
    (site_data_dir / "universe.json").write_text(json.dumps(universe, indent=2), encoding="utf-8")
    market_db_path = data_dir / "market" / "market_data.sqlite"
    market_rows_path = data_dir / "training" / "yahoo_market_rows.jsonl"
    latest_close = _latest_market_values(market_db_path, symbols)
    latest_close_source = "market/market_data.sqlite:daily_bars"
    latest_close_mode = "preferred"
    if not latest_close:
        latest_close = _latest_close_from_rows(market_rows_path, symbols)
        latest_close_source = "training/yahoo_market_rows.jsonl:fallback"
        latest_close_mode = "fallback"
        logging.warning(
            "latest_close fallback activated: sqlite source missing/empty at %s, using %s",
            market_db_path,
            market_rows_path,
        )
    symbol_count = len(symbols)
    close_count = len(latest_close)
    close_coverage = _compute_coverage(symbol_count, close_count)
    close_max_ts = _max_source_timestamp(latest_close)

    logging.info(
        "Forecast source stats | universe=%s latest_close=%s (coverage=%.2f%%, max_ts=%s)",
        symbol_count,
        close_count,
        close_coverage * 100.0,
        close_max_ts,
    )

    alerts: list[str] = []
    if close_coverage == 0.0:
        alerts.append("latest_close coverage is 0%")

    latest_predictions_raw = dashboard_payload.get("latest_predictions", [])
    latest_predictions = latest_predictions_raw if isinstance(latest_predictions_raw, list) else []
    opportunities = sorted(
        [opp for row in latest_predictions if (opp := _opportunity_row(row)) is not None],
        key=lambda x: (x["opportunity_score"], x["spread_relative"], x["spread"]),
        reverse=True,
    )

    forecasts = {
        "as_of": latest_predictions[0].get("as_of") if latest_predictions else None,
        "contract": dashboard_payload.get("prediction_contract", MULTI_HORIZON_PREDICTION_CONTRACT),
        "rows": latest_predictions,
        "latest_close": latest_close,
        "source_metadata": {
            "latest_close": {
                "source": latest_close_source,
                "mode": latest_close_mode,
                "as_of": close_max_ts,
            },
            "intraday_prices": {"status": "NOT_COLLECTED", "source": None},
        },
        "top_opportunities": opportunities[:10],
    }
    if alerts:
        forecasts["data_health"] = {
            "status": "DEGRADED",
            "alerts": alerts,
            "sources": {
                "latest_close": {
                    "symbols": close_count,
                    "coverage_pct": round(close_coverage * 100.0, 2),
                    "max_timestamp": close_max_ts,
                },
            },
        }
    (site_data_dir / "forecasts.json").write_text(json.dumps(_sanitize(forecasts), indent=2), encoding="utf-8")
    (site_data_dir / "opportunities.json").write_text(json.dumps(_sanitize(opportunities[:10]), indent=2), encoding="utf-8")

    reports_dir = data_dir / "reports"
    review_summary = _read_json(
        data_dir / "training" / "review_summary_latest.json",
        {"suite_version": "", "models": {}, "as_of": None, "suite_status": "UNKNOWN", "suite_recommendation": "PENDING"},
    )

    drift = _review_drift_payload(review_summary)
    if drift is None:
        retraining_default = {
            "status": "UNKNOWN",
            "decision": "PENDING",
            "drift_level": "UNKNOWN",
            "thresholds_disparados": [],
        }
        retraining_src = _latest_report_file(reports_dir, "retraining_review_*.json")
        retraining = (
            _read_json(retraining_src, retraining_default)
            if retraining_src
            else retraining_default
        )
        drift = {
            "status": retraining.get("status", "UNKNOWN"),
            "decision": retraining.get("decision", "PENDING"),
            "drift_level": retraining.get("drift_level", "UNKNOWN"),
            "thresholds": retraining.get("thresholds_disparados", []),
            "metrics": retraining.get("metrics", {}),
            "source_file": retraining_src.name if retraining_src else None,
            "source_date": (
                retraining.get("as_of")
                or retraining.get("date")
                or retraining.get("generated_at")
            ),
            "source_kind": "legacy_retraining_report",
        }
    (site_data_dir / "drift.json").write_text(
        json.dumps(_sanitize(drift), indent=2),
        encoding="utf-8",
    )

    incident_payload = _operational_incident_payload(metrics_payload)
    if incident_payload is None:
        incident_default = {
            "status": "UNKNOWN",
            "severity": "UNKNOWN",
            "summary": {"symptom": "No current incident evidence"},
            "impact": {},
        }
        incident_src = _latest_report_file(reports_dir, "incident_review_*.json")
        incident_payload = (
            _read_json(incident_src, incident_default)
            if incident_src
            else dict(incident_default)
        )
        incident_payload["source_file"] = incident_src.name if incident_src else None
        incident_payload["source_date"] = (
            incident_payload.get("as_of")
            or incident_payload.get("date")
            or incident_payload.get("generated_at")
        )
        incident_payload["source_kind"] = "legacy_incident_report"
    (site_data_dir / "incidents.json").write_text(
        json.dumps(_sanitize(incident_payload), indent=2),
        encoding="utf-8",
    )
    retraining_cfg = _read_json(
        data_dir / "reports" / "retraining_config_snapshot.json",
        {},
    )
    review_cfg = (
        retraining_cfg.get("review", {})
        if isinstance(retraining_cfg, dict)
        and isinstance(retraining_cfg.get("review"), dict)
        else {}
    )
    cadence_days = int(review_cfg.get("cadence_days", 14) or 14)
    retrain_soon_sessions = int(
        review_cfg.get("retrain_soon_sessions", 3) or 3
    )
    if cadence_days <= 0:
        cadence_days = _read_cadence_days(Path("config/retraining.yaml"), 14)
    if retrain_soon_sessions <= 0:
        retrain_soon_sessions = _read_retrain_soon_sessions(
            Path("config/retraining.yaml"),
            3,
        )

    model_timeline = []
    for row in _read_jsonl(data_dir / "training" / "reviews.jsonl")[-30:]:
        model_timeline.append(
            {
                "as_of": row.get("as_of"),
                "model_name": row.get("model_name", "unknown"),
                "model_key": row.get("model_key", "unknown"),
                "action": row.get("recommendation", row.get("action")),
                "status": row.get("status", "OK"),
                "drift_level": row.get("drift_level", "GREEN"),
                "current_version": row.get("current_version", "unknown"),
            }
        )

    review_models = review_summary.get("models", {}) if isinstance(review_summary.get("models"), dict) else {}
    artifacts = {
        "d1": _read_json(data_dir / "training" / "models" / "d1_champion.json", {}),
        "w1": _read_json(data_dir / "training" / "models" / "w1_champion.json", {}),
        "q1": _read_json(data_dir / "training" / "models" / "q1_champion.json", {}),
        "value": _read_json(data_dir / "training" / "models" / "value_champion.json", {}),
        "timing": _read_json(data_dir / "training" / "models" / "timing_champion.json", {}),
    }
    competitions = {
        task: _read_json(
            data_dir / "training" / "models" / f"{task}_competition.json",
            {},
        )
        for task in ("d1", "w1", "q1")
    }
    fallback_champions = _champion_versions_from_artifacts(artifacts)
    fallback_suite_version = _fallback_suite_version_from_artifacts(artifacts)
    review_summary_stale = _review_summary_is_stale(review_models, artifacts)
    # A stale review remains useful as historical evidence, but it must never
    # override the identity or metrics of the champion artifacts currently
    # served from Git. Once versions diverge, render artifact truth and expose
    # the review only through sync_status/timeline until a fresh review exists.
    effective_review_models = {} if review_summary_stale else review_models
    model_details = {
        model_key: _build_model_detail(
            model_key,
            effective_review_models.get(model_key),
            artifacts.get(model_key),
            competitions.get(model_key),
        )
        for model_key in ("d1", "w1", "q1", "value", "timing")
    }
    model_details["m3"] = _build_m3_detail(model_details["value"], model_details["timing"])

    last_review_at = review_summary.get("as_of")
    if not last_review_at and model_timeline:
        last_review_at = model_timeline[-1].get("as_of")

    champion_from_forecasts = latest_predictions[0].get("model_version", "unknown") if latest_predictions else "unknown"
    champion = review_summary.get("suite_version")
    if not champion or review_summary_stale:
        champion = fallback_suite_version if fallback_suite_version else champion_from_forecasts
    if not champion:
        champion = champion_from_forecasts

    suite_status = review_summary.get("suite_status", "UNKNOWN")
    suite_recommendation = review_summary.get("suite_recommendation", "PENDING")
    has_review_evidence = any(
        isinstance(review_models.get(task), dict) and bool(review_models.get(task))
        for task in ("d1", "w1", "q1", "value", "timing")
    )
    has_artifact_evidence = any(bool(artifacts.get(task)) for task in artifacts)
    if review_summary_stale:
        suite_status = "STALE_REVIEW"
        suite_recommendation = "REBUILD_SITE_DATA"
    elif suite_status == "UNKNOWN" and has_artifact_evidence and not has_review_evidence:
        suite_status = "UNREVIEWED"
        suite_recommendation = "REVIEW_PENDING"

    models = {
        "champion": champion,
        "timeline": model_timeline,
        "health": metrics_payload,
        "champions": {
            task: {
                **fallback_champions[task],
                **(
                    effective_review_models.get(task, {})
                    if isinstance(effective_review_models.get(task), dict)
                    else {}
                ),
            }
            for task in ("d1", "w1", "q1", "value", "timing", "m3")
        },
        "suite_status": suite_status,
        "suite_recommendation": suite_recommendation,
        "sync_status": {
            "review_summary_stale": review_summary_stale,
            "latest_model_artifact_at": _latest_model_artifact_timestamp(artifacts),
            "review_suite_version": review_summary.get("suite_version"),
            "serving_source": "champion_artifacts",
        },
        "retraining_schedule": _compute_retraining_schedule(
            last_review_at,
            cadence_days,
            str(suite_recommendation),
            retrain_soon_sessions,
        ),
        "details": model_details,
    }
    (site_data_dir / "models.json").write_text(json.dumps(_sanitize(models), indent=2), encoding="utf-8")


def mirror_site_tree(source_site_dir: Path, target_site_dir: Path) -> None:
    """Copy the generated static site tree into another location (e.g. docs/)."""
    target_site_dir.mkdir(parents=True, exist_ok=True)
    for src_path in source_site_dir.rglob("*"):
        if src_path.is_dir():
            continue
        rel = src_path.relative_to(source_site_dir)
        dst_path = target_site_dir / rel
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_path, dst_path)



def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--site-data-dir", default="site/data")
    parser.add_argument("--universe-path", default="config/universe.yaml")
    parser.add_argument("--mirror-site-dir", default=None)
    args = parser.parse_args()
    site_data_dir = Path(args.site_data_dir)
    build_pages_data(Path(args.data_dir), site_data_dir, Path(args.universe_path))
    if args.mirror_site_dir:
        mirror_site_tree(site_data_dir.parent, Path(args.mirror_site_dir))


if __name__ == "__main__":
    main()
