from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from floor.schemas import MULTI_HORIZON_PREDICTION_CONTRACT
from floor.storage import load_jsonl_rows
from floor.universe import parse_universe_yaml
from models.horizon_timing import ALLOWED_CLASSES
from utils.market_data_guard import validate_market_data_freshness
from utils.pages_build import build_pages_data

ET = ZoneInfo("America/New_York")
AUDIT_SCHEMA_VERSION = 2
AUDIT_SCRIPT_TAG = '<script type="module" src="assets/audit.js"></script>'
EXPECTED_SITE_JSON = (
    "dashboard.json",
    "drift.json",
    "forecasts.json",
    "incidents.json",
    "metrics.json",
    "models.json",
    "opportunities.json",
    "strategy.json",
    "universe.json",
)


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _parse_dt(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _finite(value: object) -> float | None:
    if not isinstance(value, (int, float, str, bytes, bytearray)):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _strict_int(value: object) -> int | None:
    numeric = _finite(value)
    if numeric is None or not numeric.is_integer():
        return None
    return int(numeric)


def _read_prediction_history(data_dir: Path) -> tuple[list[dict[str, Any]], str]:
    db_path = data_dir / "persistence" / "app.sqlite"
    if db_path.exists():
        try:
            with sqlite3.connect(db_path) as conn:
                exists = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='predictions' LIMIT 1"
                ).fetchone()
                if exists:
                    rows = conn.execute(
                        "SELECT id, payload_json FROM predictions ORDER BY id ASC"
                    ).fetchall()
                    payloads: list[dict[str, Any]] = []
                    for row_id, raw in rows:
                        try:
                            payload = json.loads(raw)
                        except (TypeError, json.JSONDecodeError):
                            continue
                        if isinstance(payload, dict):
                            item = _mapping(payload)
                            item["_audit_row_id"] = int(row_id)
                            payloads.append(item)
                    if payloads:
                        return payloads, "sqlite:predictions"
        except sqlite3.Error:
            pass

    payloads = []
    row_id = 0
    for path in sorted((data_dir / "predictions").glob("*.jsonl")):
        for payload in load_jsonl_rows(path):
            if not isinstance(payload, dict):
                continue
            row_id += 1
            item = _mapping(payload)
            item["_audit_row_id"] = row_id
            payloads.append(item)
    return payloads, "jsonl:predictions"


def _expected_horizons() -> tuple[str, ...]:
    raw = MULTI_HORIZON_PREDICTION_CONTRACT.get(
        "horizons",
        ["d1", "w1", "q1", "m3"],
    )
    if not isinstance(raw, (list, tuple)):
        return ("d1", "w1", "q1", "m3")
    return tuple(str(value).lower() for value in raw)


def select_latest_global_batch(
    rows: list[dict],
    symbols: list[str],
    horizons: tuple[str, ...] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Select the newest global batch and never hide it behind an older batch."""

    horizons = horizons or _expected_horizons()
    expected_symbols = {str(symbol).upper() for symbol in symbols}
    expected_keys = {
        (symbol, horizon)
        for symbol in expected_symbols
        for horizon in horizons
    }

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    parsed_by_raw: dict[str, datetime] = {}
    malformed_as_of = 0
    for source_row in rows:
        row = _mapping(source_row)
        raw = str(row.get("as_of") or "").strip()
        parsed = _parse_dt(raw)
        if parsed is None:
            malformed_as_of += 1
            continue
        grouped[raw].append(row)
        parsed_by_raw[raw] = parsed

    if not grouped:
        return [], {
            "status": "BLOCKED",
            "reason": "no_valid_prediction_batches",
            "as_of": None,
            "expected_rows": len(expected_keys),
            "observed_rows": 0,
            "missing_keys": sorted(f"{s}:{h}" for s, h in expected_keys),
            "extra_keys": [],
            "duplicate_keys": [],
            "malformed_as_of_rows": malformed_as_of,
        }

    latest_raw = max(grouped, key=lambda raw: parsed_by_raw[raw])
    batch = grouped[latest_raw]
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in batch:
        symbol = str(row.get("symbol") or "").upper()
        horizon = str(row.get("horizon") or "").lower()
        if symbol and horizon:
            by_key[(symbol, horizon)].append(row)

    observed_keys = set(by_key)
    missing = sorted(expected_keys - observed_keys)
    extra = sorted(observed_keys - expected_keys)
    duplicates = sorted(key for key, items in by_key.items() if len(items) != 1)
    complete = not missing and not extra and not duplicates

    clean_batch: list[dict[str, Any]] = []
    if complete:
        for symbol, horizon in sorted(expected_keys):
            item = dict(by_key[(symbol, horizon)][0])
            item.pop("_audit_row_id", None)
            clean_batch.append(item)

    return clean_batch, {
        "status": "OK" if complete else "BLOCKED",
        "reason": "complete_global_batch" if complete else "latest_global_batch_incomplete",
        "as_of": latest_raw,
        "expected_rows": len(expected_keys),
        "observed_rows": len(batch),
        "observed_unique_keys": len(observed_keys),
        "missing_keys": [f"{s}:{h}" for s, h in missing],
        "extra_keys": [f"{s}:{h}" for s, h in extra],
        "duplicate_keys": [f"{s}:{h}" for s, h in duplicates],
        "malformed_as_of_rows": malformed_as_of,
    }


def _artifact(data_dir: Path, task: str) -> dict[str, Any]:
    payload = _load_json(
        data_dir / "training" / "models" / f"{task}_champion.json",
        {},
    )
    return _mapping(payload)


def _classic_compatibility(
    task: str,
    artifact: dict[str, Any],
) -> tuple[bool, str]:
    params = _mapping(artifact.get("params"))
    if int(params.get("schema_version") or 0) != 2:
        return False, "missing classic schema_version=2 params"
    if not _mapping(params.get("floor")) or not _mapping(params.get("ceiling")):
        return False, "missing trained floor/ceiling params"
    timing = _mapping(params.get("timing"))
    if not timing:
        return False, "missing timing artifact"
    if (
        int(timing.get("schema_version") or 0) != 2
        or str(timing.get("horizon") or "") != task
    ):
        return False, "timing schema/horizon mismatch"
    status = str(timing.get("status") or "")
    if task == "d1":
        if status not in {"trained", "unavailable_daily_resolution"}:
            return False, f"unexpected d1 timing status={status or 'missing'}"
    elif status != "trained":
        return False, f"{task} timing is not trained"
    return True, "compatible"


def model_suite_compatibility(data_dir: Path) -> dict[str, Any]:
    details: dict[str, dict[str, Any]] = {}
    for task in ("d1", "w1", "q1"):
        artifact = _artifact(data_dir, task)
        ok, reason = _classic_compatibility(task, artifact)
        details[task] = {
            "compatible": ok,
            "reason": reason,
            "version": artifact.get("version"),
            "model_name": artifact.get("model_name"),
        }

    value = _artifact(data_dir, "value")
    value_params = _mapping(value.get("params"))
    value_ok = bool(
        int(value_params.get("schema_version") or 0) == 2
        and value_params.get("target_space") == "relative_floor_delta"
    )
    details["value"] = {
        "compatible": value_ok,
        "reason": (
            "compatible"
            if value_ok
            else "m3 value must use schema v2 relative_floor_delta"
        ),
        "version": value.get("version"),
        "model_name": value.get("model_name"),
    }

    timing = _artifact(data_dir, "timing")
    timing_params = _mapping(timing.get("params"))
    timing_ok = bool(
        int(timing_params.get("schema_version") or 0) == 2
        and timing_params.get("model_type") == "multinomial_logistic"
        and int(timing_params.get("class_count") or 0) == 13
    )
    details["timing"] = {
        "compatible": timing_ok,
        "reason": (
            "compatible"
            if timing_ok
            else "m3 timing must be schema v2 multinomial_logistic with 13 classes"
        ),
        "version": timing.get("version"),
        "model_name": timing.get("model_name"),
    }

    compatible = all(bool(item["compatible"]) for item in details.values())
    return {
        "status": "OK" if compatible else "RETRAIN_REQUIRED",
        "compatible": compatible,
        "tasks": details,
    }


def _valid_timing(value: object, horizon: str) -> bool:
    if horizon == "d1":
        return value in (None, "") or str(value) in ALLOWED_CLASSES["d1"]
    numeric = _strict_int(value)
    if numeric is None:
        return False
    upper = 5 if horizon == "w1" else 10
    return 1 <= numeric <= upper


def validate_prediction_contract(
    rows: list[dict],
    symbols: list[str],
) -> dict[str, Any]:
    errors: list[str] = []
    versions = {str(row.get("model_version") or "").strip() for row in rows}
    as_of_values = {str(row.get("as_of") or "").strip() for row in rows}
    expected = len(symbols) * len(_expected_horizons())
    if len(rows) != expected:
        errors.append(f"row_count={len(rows)} expected={expected}")
    if len(as_of_values) != 1:
        errors.append(f"mixed_as_of_count={len(as_of_values)}")
    if len(versions) != 1 or "" in versions:
        errors.append(f"mixed_or_missing_model_versions={sorted(versions)}")

    for row in rows:
        symbol = str(row.get("symbol") or "").upper()
        horizon = str(row.get("horizon") or "").lower()
        label = f"{symbol}:{horizon}"
        if horizon in {"d1", "w1", "q1"}:
            floor = _finite(row.get("floor_value"))
            ceiling = _finite(row.get("ceiling_value"))
            if (
                floor is None
                or ceiling is None
                or floor <= 0
                or ceiling <= 0
                or floor > ceiling
            ):
                errors.append(f"{label}:invalid_floor_ceiling")
            if not _valid_timing(row.get("floor_time_bucket"), horizon):
                errors.append(
                    f"{label}:invalid_floor_timing={row.get('floor_time_bucket')}"
                )
            if not _valid_timing(row.get("ceiling_time_bucket"), horizon):
                errors.append(
                    f"{label}:invalid_ceiling_timing={row.get('ceiling_time_bucket')}"
                )
        elif horizon == "m3":
            if str(row.get("m3_status") or "") != "ok":
                errors.append(f"{label}:m3_status={row.get('m3_status')}")
                continue
            floor = _finite(row.get("floor_m3"))
            week = _strict_int(row.get("floor_week_m3"))
            confidence = _finite(row.get("floor_week_m3_confidence"))
            if floor is None or floor <= 0:
                errors.append(f"{label}:invalid_floor_m3")
            if week is None or not 1 <= week <= 13:
                errors.append(
                    f"{label}:invalid_floor_week_m3={row.get('floor_week_m3')}"
                )
            if confidence is None or not 0.0 <= confidence <= 1.0:
                errors.append(f"{label}:invalid_m3_confidence")
            top3 = row.get("floor_week_m3_top3")
            if not isinstance(top3, list) or len(top3) != 3:
                errors.append(f"{label}:m3_top3_must_have_3_rows")
            else:
                top_weeks: list[int] = []
                for item_raw in top3:
                    item = _mapping(item_raw)
                    if not item:
                        errors.append(f"{label}:invalid_m3_top3_item")
                        continue
                    item_week = _strict_int(item.get("week"))
                    probability = _finite(item.get("probability"))
                    if item_week is None or not 1 <= item_week <= 13:
                        errors.append(f"{label}:invalid_m3_top3_week")
                    else:
                        top_weeks.append(item_week)
                    if probability is None or not 0.0 <= probability <= 1.0:
                        errors.append(f"{label}:invalid_m3_top3_probability")
                if len(top_weeks) != len(set(top_weeks)):
                    errors.append(f"{label}:duplicate_m3_top3_week")
        else:
            errors.append(f"{label}:unknown_horizon")

    return {
        "status": "OK" if not errors else "BLOCKED",
        "valid": not errors,
        "errors": errors[:100],
        "error_count": len(errors),
        "model_version": next(iter(versions)) if len(versions) == 1 else None,
        "as_of": next(iter(as_of_values)) if len(as_of_values) == 1 else None,
    }


def _freshness(
    data_dir: Path,
    symbols: list[str],
    batch_as_of: object,
) -> dict[str, Any]:
    db_path = data_dir / "market" / "market_data.sqlite"
    try:
        market = validate_market_data_freshness(
            db_path,
            symbols,
            max_stale_sessions=0,
        )
    except RuntimeError as exc:
        return {"status": "BLOCKED", "fresh": False, "reason": str(exc)}

    batch_dt = _parse_dt(batch_as_of)
    if batch_dt is None:
        return {
            "status": "BLOCKED",
            "fresh": False,
            "reason": "prediction batch as_of is malformed",
            "market": market,
        }
    required = str(market.get("required_latest_session") or "")
    batch_day = batch_dt.astimezone(ET).date().isoformat()
    if required and batch_day < required:
        return {
            "status": "BLOCKED",
            "fresh": False,
            "reason": (
                f"prediction batch date {batch_day} is older than "
                f"required market session {required}"
            ),
            "batch_session": batch_day,
            "market": market,
        }
    return {
        "status": "OK",
        "fresh": True,
        "batch_session": batch_day,
        "market": market,
    }


def _normalize_monitoring_payloads(site_data_dir: Path) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    details: dict[str, Any] = {}

    drift_path = site_data_dir / "drift.json"
    drift = _mapping(_load_json(drift_path, {}))
    drift_source = drift.get("source_date")
    drift_dt = _parse_dt(drift_source)
    if not drift.get("source_file") or drift_dt is None:
        drift.update(
            {
                "status": "UNKNOWN",
                "decision": "PENDING",
                "drift_level": "UNKNOWN",
                "source_file": None,
                "source_date": drift_source,
            }
        )
        details["drift"] = {
            "status": "UNKNOWN",
            "source_date": drift_source,
            "age_days": None,
        }
    else:
        age_days = max(0.0, (now - drift_dt).total_seconds() / 86400.0)
        details["drift"] = {
            "status": "STALE" if age_days > 30 else "OK",
            "source_date": drift_source,
            "age_days": round(age_days, 2),
        }
    _write_json(drift_path, drift)

    incident_path = site_data_dir / "incidents.json"
    incident = _mapping(_load_json(incident_path, {}))
    incident_source = incident.get("source_date")
    incident_dt = _parse_dt(incident_source)
    if not incident.get("source_file") or incident_dt is None:
        incident.update(
            {
                "status": "UNKNOWN",
                "severity": "UNKNOWN",
                "source_file": None,
                "source_date": incident_source,
            }
        )
        summary = _mapping(incident.get("summary"))
        summary["symptom"] = "No incident report available"
        incident["summary"] = summary
        details["incidents"] = {
            "status": "UNKNOWN",
            "source_date": incident_source,
            "age_days": None,
        }
    else:
        age_days = max(0.0, (now - incident_dt).total_seconds() / 86400.0)
        details["incidents"] = {
            "status": "STALE" if age_days > 30 else "OK",
            "source_date": incident_source,
            "age_days": round(age_days, 2),
        }
    _write_json(incident_path, incident)

    degraded = any(item.get("status") != "OK" for item in details.values())
    return {"status": "DEGRADED" if degraded else "OK", "sources": details}


def _q1_range_rank(rows: list[dict]) -> list[dict[str, Any]]:
    """Descriptive q1 range ranking; this is explicitly not an alpha score."""
    out: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("horizon") or "").lower() != "q1":
            continue
        floor = _finite(row.get("floor_value"))
        ceiling = _finite(row.get("ceiling_value"))
        confidence = _finite(row.get("confidence_score"))
        if floor is None or ceiling is None or floor <= 0 or ceiling < floor:
            continue
        midpoint = (floor + ceiling) / 2.0
        spread = ceiling - floor
        relative = spread / max(abs(midpoint), 1e-9)
        bounded_confidence = max(
            0.0,
            min(1.0, confidence if confidence is not None else 0.0),
        )
        out.append(
            {
                "symbol": row.get("symbol"),
                "horizon": "q1",
                "floor": round(floor, 4),
                "ceiling": round(ceiling, 4),
                "spread": round(spread, 4),
                "spread_relative": round(relative, 6),
                "spread_relative_pct": round(relative * 100.0, 2),
                "confidence": round(bounded_confidence, 4),
                "opportunity_score": round(relative * bounded_confidence, 6),
                "ranking_basis": "descriptive_q1_range_score_not_alpha",
                "as_of": row.get("as_of"),
            }
        )
    return sorted(
        out,
        key=lambda item: float(item["opportunity_score"]),
        reverse=True,
    )[:10]


def _inject_audit_script(site_dir: Path) -> int:
    changed = 0
    for path in sorted(site_dir.glob("*.html")):
        text = path.read_text(encoding="utf-8")
        if AUDIT_SCRIPT_TAG in text:
            continue
        if "</body>" not in text:
            raise RuntimeError(f"Pages HTML missing </body>: {path}")
        path.write_text(
            text.replace("</body>", f"{AUDIT_SCRIPT_TAG}</body>"),
            encoding="utf-8",
        )
        changed += 1
    return changed


def _site_schema_audit(
    site_data_dir: Path,
    symbols: list[str],
) -> dict[str, Any]:
    errors: list[str] = []
    for name in EXPECTED_SITE_JSON:
        path = site_data_dir / name
        if not path.exists():
            errors.append(f"missing:{name}")
            continue
        payload = _load_json(path, None)
        if payload is None:
            errors.append(f"invalid_json:{name}")

    universe = _mapping(_load_json(site_data_dir / "universe.json", {}))
    published_symbols = universe.get("symbols")
    if not isinstance(published_symbols, list):
        errors.append("universe.symbols_not_list")
    else:
        expected_symbols = [str(symbol) for symbol in symbols]
        if published_symbols != expected_symbols:
            errors.append("universe.symbols_mismatch")

    dashboard = _mapping(_load_json(site_data_dir / "dashboard.json", {}))
    if "system_health" not in dashboard:
        errors.append("dashboard.missing_system_health")
    if not isinstance(dashboard.get("latest_predictions", []), list):
        errors.append("dashboard.latest_predictions_not_list")

    forecasts = _mapping(_load_json(site_data_dir / "forecasts.json", {}))
    if not isinstance(forecasts.get("rows", []), list):
        errors.append("forecasts.rows_not_list")
    if not isinstance(forecasts.get("top_opportunities", []), list):
        errors.append("forecasts.top_opportunities_not_list")

    models = _mapping(_load_json(site_data_dir / "models.json", {}))
    if "suite_status" not in models:
        errors.append("models.missing_suite_status")

    drift = _mapping(_load_json(site_data_dir / "drift.json", {}))
    if not str(drift.get("drift_level") or ""):
        errors.append("drift.missing_drift_level")

    incidents = _mapping(_load_json(site_data_dir / "incidents.json", {}))
    if not str(incidents.get("status") or ""):
        errors.append("incidents.missing_status")

    strategy = _mapping(_load_json(site_data_dir / "strategy.json", {}))
    if "status" not in strategy:
        errors.append("strategy.missing_status")

    metrics = _mapping(_load_json(site_data_dir / "metrics.json", {}))
    if "status" not in metrics and not metrics:
        errors.append("metrics.empty_without_status")

    return {
        "status": "OK" if not errors else "BLOCKED",
        "valid": not errors,
        "errors": errors,
        "error_count": len(errors),
    }


def publish_pages_data(
    data_dir: Path,
    site_data_dir: Path,
    universe_path: Path,
    *,
    source_commit: str = "",
) -> dict[str, Any]:
    build_pages_data(data_dir, site_data_dir, universe_path)
    site_dir = site_data_dir.parent
    symbols = list(parse_universe_yaml(universe_path))
    monitoring_audit = _normalize_monitoring_payloads(site_data_dir)

    history, history_source = _read_prediction_history(data_dir)
    batch, batch_audit = select_latest_global_batch(history, symbols)
    model_audit = model_suite_compatibility(data_dir)
    contract_audit = (
        validate_prediction_contract(batch, symbols)
        if batch
        else {
            "status": "BLOCKED",
            "valid": False,
            "errors": ["no_complete_global_batch"],
            "error_count": 1,
            "model_version": None,
            "as_of": batch_audit.get("as_of"),
        }
    )
    freshness_audit = _freshness(data_dir, symbols, batch_audit.get("as_of"))
    schema_audit = _site_schema_audit(site_data_dir, symbols)

    blockers: list[str] = []
    if batch_audit.get("status") != "OK":
        blockers.append(str(batch_audit.get("reason") or "prediction_batch_blocked"))
    if not bool(model_audit.get("compatible")):
        blockers.append("champion_model_suite_requires_retraining")
    if not bool(contract_audit.get("valid")):
        blockers.append("prediction_contract_invalid")
    if not bool(freshness_audit.get("fresh")):
        blockers.append("market_or_prediction_data_stale")
    if not bool(schema_audit.get("valid")):
        blockers.append("site_payload_schema_invalid")

    warnings: list[str] = []
    if monitoring_audit.get("status") != "OK":
        warnings.append("monitoring_report_missing_or_stale")

    publishable = not blockers
    forecasts_path = site_data_dir / "forecasts.json"
    forecasts = _mapping(_load_json(forecasts_path, {}))
    dashboard_path = site_data_dir / "dashboard.json"
    dashboard = _mapping(_load_json(dashboard_path, {}))
    models_path = site_data_dir / "models.json"
    models = _mapping(_load_json(models_path, {}))
    opportunities_path = site_data_dir / "opportunities.json"

    if publishable:
        opportunities = _q1_range_rank(batch)
        forecasts["as_of"] = batch_audit.get("as_of")
        forecasts["rows"] = batch
        forecasts["top_opportunities"] = opportunities
        forecasts["publishable"] = True
        dashboard["latest_predictions"] = batch
        dashboard["system_health"] = "OK" if not warnings else "DEGRADED"
        _write_json(opportunities_path, opportunities)
    else:
        prior_rows = forecasts.get("rows", [])
        suppressed = len(prior_rows) if isinstance(prior_rows, list) else 0
        forecasts["rows"] = []
        forecasts["top_opportunities"] = []
        forecasts["publishable"] = False
        forecasts["suppressed_rows"] = suppressed
        forecasts["suppression_reasons"] = blockers
        forecasts["as_of"] = batch_audit.get("as_of")
        dashboard["latest_predictions"] = []
        dashboard["system_health"] = "BLOCKED"
        _write_json(opportunities_path, [])

    if warnings and publishable:
        data_health = _mapping(forecasts.get("data_health"))
        data_health["status"] = "DEGRADED"
        existing = data_health.get("alerts", [])
        alerts = list(existing) if isinstance(existing, list) else []
        data_health["alerts"] = sorted(set([*alerts, *warnings]))
        forecasts["data_health"] = data_health

    models["publication_model_compatibility"] = model_audit
    if not bool(model_audit.get("compatible")):
        models["suite_status"] = "RETRAIN_REQUIRED"
        models["suite_recommendation"] = "RETRAIN_ALL_MODELS"

    audit: dict[str, Any] = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": source_commit or None,
        "safe_to_deploy": True,
        "publishable_forecasts": publishable,
        "status": "BLOCKED" if blockers else ("DEGRADED" if warnings else "OK"),
        "blockers": blockers,
        "warnings": warnings,
        "prediction_history_source": history_source,
        "batch": batch_audit,
        "prediction_contract": contract_audit,
        "models": model_audit,
        "freshness": freshness_audit,
        "monitoring": monitoring_audit,
        "site_schema": schema_audit,
        "expected_symbols": len(symbols),
        "expected_horizons": list(_expected_horizons()),
        "expected_prediction_rows": len(symbols) * len(_expected_horizons()),
    }

    publication_audit = {
        "status": audit["status"],
        "publishable_forecasts": publishable,
        "batch_as_of": batch_audit.get("as_of"),
        "source_commit": source_commit or None,
        "blockers": blockers,
        "warnings": warnings,
    }
    dashboard["publication_audit"] = publication_audit
    forecasts["publication_audit"] = publication_audit
    models["publication_audit"] = publication_audit

    _write_json(forecasts_path, forecasts)
    _write_json(dashboard_path, dashboard)
    _write_json(models_path, models)
    _write_json(site_data_dir / "audit.json", audit)

    injected = _inject_audit_script(site_dir)
    audit["html_files_injected"] = injected
    _write_json(site_data_dir / "audit.json", audit)
    return audit


def validate_published_site(site_data_dir: Path) -> dict[str, Any]:
    audit = _mapping(_load_json(site_data_dir / "audit.json", {}))
    if not audit or not audit.get("safe_to_deploy"):
        raise RuntimeError("Pages publication audit is missing or unsafe")

    forecasts = _mapping(_load_json(site_data_dir / "forecasts.json", {}))
    rows = forecasts.get("rows", [])
    if not isinstance(rows, list):
        raise RuntimeError("Pages forecasts rows must be a list")

    opportunities = _load_json(site_data_dir / "opportunities.json", None)
    if not isinstance(opportunities, list):
        raise RuntimeError("Pages opportunities payload must be a list")

    publishable = bool(audit.get("publishable_forecasts"))
    if publishable:
        expected = int(audit.get("expected_prediction_rows") or 0)
        if len(rows) != expected or expected <= 0:
            raise RuntimeError(
                f"Publishable Pages payload row mismatch rows={len(rows)} "
                f"expected={expected}"
            )
        if audit.get("status") not in {"OK", "DEGRADED"}:
            raise RuntimeError("Publishable Pages payload has blocked audit status")
    else:
        if rows:
            raise RuntimeError(
                "Blocked Pages publication must suppress actionable forecast rows"
            )
        if opportunities:
            raise RuntimeError(
                "Blocked Pages publication must suppress opportunities payload"
            )

    if not bool(_mapping(audit.get("site_schema")).get("valid")):
        raise RuntimeError("Pages static payload schema audit failed")
    return audit


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build and aggressively audit GitHub Pages data"
    )
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--site-data-dir", default="site/data")
    parser.add_argument("--universe-path", default="config/universe.yaml")
    parser.add_argument("--source-commit", default="")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()

    try:
        if args.validate_only:
            audit = validate_published_site(Path(args.site_data_dir))
        else:
            audit = publish_pages_data(
                Path(args.data_dir),
                Path(args.site_data_dir),
                Path(args.universe_path),
                source_commit=args.source_commit,
            )
            validate_published_site(Path(args.site_data_dir))
    except (RuntimeError, ValueError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False))
        return 1

    print(json.dumps(audit, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
