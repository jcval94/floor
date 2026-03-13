from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from floor.universe import parse_universe_yaml

ALLOWED_KEYS = {
    "prediction_files",
    "signal_files",
    "latest_predictions",
    "generated_at",
    "system_health",
}

SENSITIVE_KEYS = {
    "api_key",
    "secret",
    "token",
    "password",
    "authorization",
}



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


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


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

    # Objetivo: combinar amplitud absoluta, amplitud relativa y probabilidad temporal.
    # Favorece oportunidades amplias, proporcionales al precio y con mejor soporte probabilístico.
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


def build_pages_data(data_dir: Path, site_data_dir: Path, universe_path: Path) -> None:
    site_data_dir.mkdir(parents=True, exist_ok=True)

    dashboard_src = data_dir / "reports" / "dashboard.json"
    dashboard_payload = {
        "prediction_files": 0,
        "signal_files": 0,
        "latest_predictions": [],
        "system_health": "UNKNOWN",
    }
    if dashboard_src.exists():
        dashboard_payload.update(json.loads(dashboard_src.read_text(encoding="utf-8")))

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

    latest_predictions_raw = dashboard_payload.get("latest_predictions", [])
    latest_predictions = latest_predictions_raw if isinstance(latest_predictions_raw, list) else []
    opportunities = sorted(
        [opp for row in latest_predictions if (opp := _opportunity_row(row)) is not None],
        key=lambda x: (x["opportunity_score"], x["spread_relative"], x["spread"]),
        reverse=True,
    )

    forecasts = {
        "as_of": latest_predictions[0].get("as_of") if latest_predictions else None,
        "rows": latest_predictions,
        "top_opportunities": opportunities[:10],
    }
    (site_data_dir / "forecasts.json").write_text(json.dumps(_sanitize(forecasts), indent=2), encoding="utf-8")
    (site_data_dir / "opportunities.json").write_text(json.dumps(_sanitize(opportunities[:10]), indent=2), encoding="utf-8")

    retraining = _read_json(data_dir / "reports" / "retraining_review_2026-03-12.json", {
        "status": "UNKNOWN", "decision": "PENDING", "drift_level": "GREEN", "thresholds_disparados": []
    })
    drift = {
        "status": retraining.get("status", "UNKNOWN"),
        "decision": retraining.get("decision", "PENDING"),
        "drift_level": retraining.get("drift_level", "GREEN"),
        "thresholds": retraining.get("thresholds_disparados", []),
        "metrics": retraining.get("metrics", {}),
    }
    (site_data_dir / "drift.json").write_text(json.dumps(_sanitize(drift), indent=2), encoding="utf-8")

    incident_payload = _read_json(data_dir / "reports" / "incident_review_2026-03-12.json", {
        "status": "OK", "severity": "SEV4", "summary": {"symptom": "No incidents"}, "impact": {}
    })
    (site_data_dir / "incidents.json").write_text(json.dumps(_sanitize(incident_payload), indent=2), encoding="utf-8")

    model_timeline = []
    for row in _read_jsonl(data_dir / "training" / "reviews.jsonl")[-30:]:
        model_timeline.append(
            {
                "as_of": row.get("as_of"),
                "model_name": row.get("model_name", "champion-v0"),
                "action": row.get("action"),
                "status": row.get("status", "OK"),
                "drift_level": row.get("drift_level", "GREEN"),
            }
        )

    models = {
        "champion": latest_predictions[0].get("model_version", "unknown") if latest_predictions else "unknown",
        "timeline": model_timeline,
        "health": metrics_payload,
    }
    (site_data_dir / "models.json").write_text(json.dumps(_sanitize(models), indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--site-data-dir", default="site/data")
    parser.add_argument("--universe-path", default="config/universe.yaml")
    args = parser.parse_args()
    build_pages_data(Path(args.data_dir), Path(args.site_data_dir), Path(args.universe_path))


if __name__ == "__main__":
    main()
