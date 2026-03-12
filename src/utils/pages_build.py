from __future__ import annotations

import argparse
import json
from pathlib import Path

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


DEFAULT_UNIVERSE_50 = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "AMD", "ORCL",
    "CRM", "ADBE", "CSCO", "QCOM", "AMAT", "TXN", "NFLX", "INTC", "IBM", "INTU",
    "PLTR", "JPM", "BAC", "WFC", "GS", "MS", "V", "MA", "AXP", "BRK.B",
    "LLY", "ABBV", "UNH", "MRK", "ABT", "TMO", "XOM", "CVX", "CAT", "GE",
    "RTX", "BA", "WMT", "COST", "HD", "PG", "KO", "MCD", "DIS", "UBER",
]


def _sanitize(obj):
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


def _read_json(path: Path, default):
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


def _parse_universe_yaml(path: Path) -> list[str]:
    if not path.exists():
        return DEFAULT_UNIVERSE_50
    symbols: list[str] = []
    in_symbols = False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("symbols:"):
            in_symbols = True
            continue
        if in_symbols and line.startswith("-"):
            symbols.append(line[1:].strip())
        elif in_symbols and line and not line.startswith("#"):
            break
    return symbols or DEFAULT_UNIVERSE_50


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
        "symbols": _parse_universe_yaml(universe_path),
    }
    (site_data_dir / "universe.json").write_text(json.dumps(universe, indent=2), encoding="utf-8")

    latest_predictions = list(dashboard_payload.get("latest_predictions", []))
    opportunities = sorted(
        [
            {
                "symbol": row.get("symbol"),
                "horizon": row.get("horizon"),
                "floor": row.get("floor_value"),
                "ceiling": row.get("ceiling_value"),
                "spread": round(float(row.get("ceiling_value", 0.0)) - float(row.get("floor_value", 0.0)), 4),
                "event_type": row.get("event_type"),
                "as_of": row.get("as_of"),
            }
            for row in latest_predictions
            if row.get("floor_value") is not None and row.get("ceiling_value") is not None
        ],
        key=lambda x: x["spread"],
        reverse=True,
    )

    forecasts = {
        "as_of": latest_predictions[0].get("as_of") if latest_predictions else None,
        "rows": latest_predictions,
        "top_opportunities": opportunities[:20],
    }
    (site_data_dir / "forecasts.json").write_text(json.dumps(_sanitize(forecasts), indent=2), encoding="utf-8")
    (site_data_dir / "opportunities.json").write_text(json.dumps(_sanitize(opportunities[:20]), indent=2), encoding="utf-8")

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
