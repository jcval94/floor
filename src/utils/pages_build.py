from __future__ import annotations

import argparse
import json
import re
import sqlite3
import shutil
from pathlib import Path
from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from typing import Any

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
# Mínimo de cobertura intraday para considerar el dataset "usable" sin alerta de frescura.
# Si queda por debajo, se publica `data_health` para que la UI no lo presente como dato actualizado.
MIN_INTRADAY_COVERAGE_RATIO_FOR_OK = 0.6


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


def _compute_retraining_schedule(last_review_at: Any, cadence_days: int) -> dict[str, Any]:
    cadence_days = max(int(cadence_days), 1)
    last_dt = _parse_iso_datetime(last_review_at)
    if last_dt is None:
        return {
            "cadence_days": cadence_days,
            "last_review_at": None,
            "next_review_at": None,
            "seconds_until_due": None,
            "human_eta": "sin fecha de última revisión",
            "is_overdue": None,
        }

    now = datetime.now(tz=timezone.utc)
    next_dt = last_dt + timedelta(days=cadence_days)
    seconds_until_due = int((next_dt - now).total_seconds())
    is_overdue = seconds_until_due < 0
    abs_seconds = abs(seconds_until_due)
    days = abs_seconds // 86400
    hours = (abs_seconds % 86400) // 3600
    prefix = "vencido hace" if is_overdue else "faltan"
    human_eta = f"{prefix} {days}d {hours}h"

    return {
        "cadence_days": cadence_days,
        "last_review_at": last_dt.isoformat(),
        "next_review_at": next_dt.isoformat(),
        "seconds_until_due": seconds_until_due,
        "human_eta": human_eta,
        "is_overdue": is_overdue,
    }


def _build_model_detail(model_key: str, review_model: Any, artifact: Any) -> dict[str, Any]:
    review_model = review_model if isinstance(review_model, dict) else {}
    artifact = artifact if isinstance(artifact, dict) else {}
    summary = review_model.get("summary", {}) if isinstance(review_model.get("summary"), dict) else {}
    performance = summary.get("performance", {}) if isinstance(summary.get("performance"), dict) else {}
    shared_data = summary.get("shared_data", {}) if isinstance(summary.get("shared_data"), dict) else {}
    target = summary.get("target", {}) if isinstance(summary.get("target"), dict) else {}
    schema = summary.get("schema", {}) if isinstance(summary.get("schema"), dict) else {}

    return {
        "model_key": model_key,
        "model_name": review_model.get("model_name", artifact.get("model_name", "unknown")),
        "current_version": review_model.get("current_version", artifact.get("version", "unknown")),
        "status": review_model.get("status", "UNKNOWN"),
        "drift_level": review_model.get("drift_level", "GREEN"),
        "recommendation": review_model.get("recommendation", review_model.get("action", "SKIP_RETRAIN")),
        "auto_retrain": bool(review_model.get("auto_retrain", False)),
        "as_of": review_model.get("as_of"),
        "reason": review_model.get("reason", ""),
        "metrics": {
            "current": performance.get("current_metrics", {}),
            "baseline": performance.get("baseline_metrics", {}),
            "deltas": performance.get("deltas", {}),
        },
        "drift_components": {
            "shared_data": {"state": shared_data.get("state"), "score": shared_data.get("score")},
            "target": {"state": target.get("state"), "score": target.get("score")},
            "schema": {"state": schema.get("state"), "score": schema.get("score")},
            "performance": {"state": performance.get("state"), "score": performance.get("score")},
        },
        "artifact": {
            "model_type": artifact.get("model_type"),
            "trained_at": artifact.get("trained_at") or artifact.get("as_of"),
            "dataset_summary": artifact.get("dataset_summary", {}),
            "params": artifact.get("params", {}),
        },
    }


def _read_cadence_days(config_path: Path, default: int = 14) -> int:
    if not config_path.exists():
        return default
    content = config_path.read_text(encoding="utf-8", errors="ignore")
    match = re.search(r"^\s*cadence_days\s*:\s*(\d+)\s*$", content, flags=re.MULTILINE)
    if not match:
        return default
    try:
        return max(int(match.group(1)), 1)
    except ValueError:
        return default


def _opportunity_row(row: dict) -> dict | None:
    floor_raw = row.get("floor_value")
    ceiling_raw = row.get("ceiling_value")
    if floor_raw is None or ceiling_raw is None:
        return None

    floor = float(floor_raw)
    ceiling = float(ceiling_raw)
    spread_abs = max(ceiling - floor, 0.0)
    midpoint = (ceiling + floor) / 2.0
    spread_rel = spread_abs / max(abs(midpoint), 1e-6)

    floor_prob = float(row.get("floor_time_probability", 0.5) or 0.5)
    ceiling_prob = float(row.get("ceiling_time_probability", 0.5) or 0.5)
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


def _latest_intraday_values(rows_path: Path, symbols: list[str]) -> dict[str, dict[str, Any]]:
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
        try:
            close_value = float(close)
        except (TypeError, ValueError):
            continue
        prev = latest.get(symbol)
        if prev is None or (ts, idx) >= (prev[0], prev[1]):
            latest[symbol] = (
                ts,
                idx,
                {
                    "as_of": ts,
                    "price": close_value,
                    "source": "training/yahoo_market_rows.jsonl",
                },
            )
    return {symbol: payload for symbol, (_, __, payload) in latest.items()}


def _max_timestamp(records: dict[str, dict[str, Any]]) -> str | None:
    max_dt: datetime | None = None
    max_raw: str | None = None
    for payload in records.values():
        ts_raw = payload.get("as_of")
        ts = _parse_iso_datetime(ts_raw)
        if ts is None:
            continue
        if max_dt is None or ts > max_dt:
            max_dt = ts
            max_raw = str(ts_raw)
    return max_raw


def _build_market_data_health(
    latest_intraday: dict[str, dict[str, Any]],
    latest_close: dict[str, dict[str, Any]],
    universe_symbols: list[str],
) -> dict[str, Any]:
    universe_count = len(universe_symbols)
    intraday_count = len(latest_intraday)
    close_count = len(latest_close)
    intraday_coverage = (intraday_count / universe_count) if universe_count else 0.0
    close_coverage = (close_count / universe_count) if universe_count else 0.0
    intraday_max_ts = _max_timestamp(latest_intraday)
    close_max_ts = _max_timestamp(latest_close)

    summary = {
        "universe_symbols": universe_count,
        "latest_intraday_count": intraday_count,
        "latest_close_count": close_count,
        "latest_intraday_max_timestamp": intraday_max_ts,
        "latest_close_max_timestamp": close_max_ts,
        "latest_intraday_coverage_pct": round(intraday_coverage * 100.0, 2),
        "latest_close_coverage_pct": round(close_coverage * 100.0, 2),
        "intraday_min_coverage_pct_for_ok": round(MIN_INTRADAY_COVERAGE_RATIO_FOR_OK * 100.0, 2),
    }

    alerts: list[dict[str, Any]] = []
    if close_coverage == 0.0:
        alerts.append(
            {
                "code": "LATEST_CLOSE_COVERAGE_ZERO",
                "message": "Sin cobertura de cierre diario (latest_close).",
                "severity": "critical",
            }
        )
    if intraday_coverage < MIN_INTRADAY_COVERAGE_RATIO_FOR_OK:
        alerts.append(
            {
                "code": "LATEST_INTRADAY_LOW_COVERAGE",
                "message": (
                    "Cobertura intraday baja "
                    f"(< {int(MIN_INTRADAY_COVERAGE_RATIO_FOR_OK * 100)}%)."
                ),
                "severity": "warning",
            }
        )

    health = {
        "status": "ok" if not alerts else "degraded",
        "summary": summary,
    }
    if alerts:
        health["alerts"] = alerts
    return health


def build_pages_data(data_dir: Path, site_data_dir: Path, universe_path: Path) -> None:
    site_data_dir.mkdir(parents=True, exist_ok=True)

    dashboard_src = data_dir / "reports" / "dashboard.json"
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

    universe = {
        "name": "us_top50_liquid_v1",
        "symbols": parse_universe_yaml(universe_path),
    }
    (site_data_dir / "universe.json").write_text(json.dumps(universe, indent=2), encoding="utf-8")
    latest_close = _latest_market_values(data_dir / "market" / "market_data.sqlite", universe["symbols"])
    latest_intraday = _latest_intraday_values(data_dir / "training" / "yahoo_market_rows.jsonl", universe["symbols"])
    market_data_health = _build_market_data_health(latest_intraday, latest_close, universe["symbols"])

    print(
        "[pages_build] market data coverage:",
        json.dumps(market_data_health["summary"], ensure_ascii=False),
    )
    if market_data_health["status"] != "ok":
        print(
            "[pages_build] market data alerts:",
            json.dumps(market_data_health.get("alerts", []), ensure_ascii=False),
        )

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
        "latest_intraday": latest_intraday,
        "latest_close": latest_close,
        "top_opportunities": opportunities[:10],
    }
    if market_data_health["status"] != "ok":
        forecasts["data_health"] = market_data_health
    (site_data_dir / "forecasts.json").write_text(json.dumps(_sanitize(forecasts), indent=2), encoding="utf-8")
    (site_data_dir / "opportunities.json").write_text(json.dumps(_sanitize(opportunities[:10]), indent=2), encoding="utf-8")

    reports_dir = data_dir / "reports"

    retraining_default = {
        "status": "UNKNOWN", "decision": "PENDING", "drift_level": "GREEN", "thresholds_disparados": []
    }
    retraining_src = _latest_report_file(reports_dir, "retraining_review_*.json")
    retraining = _read_json(retraining_src, retraining_default) if retraining_src else retraining_default
    drift: dict[str, Any] = {
        "status": retraining.get("status", "UNKNOWN"),
        "decision": retraining.get("decision", "PENDING"),
        "drift_level": retraining.get("drift_level", "GREEN"),
        "thresholds": retraining.get("thresholds_disparados", []),
        "metrics": retraining.get("metrics", {}),
        "source_file": retraining_src.name if retraining_src else None,
        "source_date": retraining.get("as_of") or retraining.get("date") or retraining.get("generated_at"),
    }
    (site_data_dir / "drift.json").write_text(json.dumps(_sanitize(drift), indent=2), encoding="utf-8")

    incident_default = {
        "status": "OK", "severity": "SEV4", "summary": {"symptom": "No incidents"}, "impact": {}
    }
    incident_src = _latest_report_file(reports_dir, "incident_review_*.json")
    incident_payload: dict[str, Any] = _read_json(incident_src, incident_default) if incident_src else dict(incident_default)
    incident_payload["source_file"] = incident_src.name if incident_src else None
    incident_payload["source_date"] = (
        incident_payload.get("as_of")
        or incident_payload.get("date")
        or incident_payload.get("generated_at")
    )
    (site_data_dir / "incidents.json").write_text(json.dumps(_sanitize(incident_payload), indent=2), encoding="utf-8")

    review_summary = _read_json(
        data_dir / "training" / "review_summary_latest.json",
        {"suite_version": "", "models": {}, "as_of": None, "suite_status": "UNKNOWN", "suite_recommendation": "PENDING"},
    )
    retraining_cfg = _read_json(data_dir / "reports" / "retraining_config_snapshot.json", {})
    if isinstance(retraining_cfg, dict):
        cadence_days = int(((retraining_cfg.get("review", {}) if isinstance(retraining_cfg.get("review"), dict) else {}).get("cadence_days", 14) or 14))
    else:
        cadence_days = 14
    if cadence_days <= 0:
        cadence_days = _read_cadence_days(Path("config/retraining.yaml"), 14)

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
        "value": _read_json(data_dir / "training" / "models" / "value_champion.json", {}),
        "timing": _read_json(data_dir / "training" / "models" / "timing_champion.json", {}),
    }
    model_details = {
        model_key: _build_model_detail(model_key, review_models.get(model_key), artifacts.get(model_key))
        for model_key in ("value", "timing")
    }

    last_review_at = review_summary.get("as_of")
    if not last_review_at and model_timeline:
        last_review_at = model_timeline[-1].get("as_of")

    models = {
        "champion": review_summary.get("suite_version") or (latest_predictions[0].get("model_version", "unknown") if latest_predictions else "unknown"),
        "timeline": model_timeline,
        "health": metrics_payload,
        "champions": review_summary.get("models", {}),
        "suite_status": review_summary.get("suite_status", "UNKNOWN"),
        "suite_recommendation": review_summary.get("suite_recommendation", "PENDING"),
        "retraining_schedule": _compute_retraining_schedule(last_review_at, cadence_days),
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
