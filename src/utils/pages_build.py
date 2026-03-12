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


def build_pages_data(data_dir: Path, site_data_dir: Path) -> None:
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

    metrics_src = data_dir / "metrics" / "public_metrics.json"
    if metrics_src.exists():
        metrics_payload = _sanitize(json.loads(metrics_src.read_text(encoding="utf-8")))
    else:
        metrics_payload = {"status": "no_public_metrics", "series": []}
    (site_data_dir / "metrics.json").write_text(json.dumps(metrics_payload, indent=2), encoding="utf-8")

    strategy_src = data_dir / "reports" / "strategy.json"
    if strategy_src.exists():
        strategy_payload = _sanitize(json.loads(strategy_src.read_text(encoding="utf-8")))
    else:
        strategy_payload = {"status": "no_strategy_report", "equity_curve": []}
    (site_data_dir / "strategy.json").write_text(json.dumps(strategy_payload, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--site-data-dir", default="site/data")
    args = parser.parse_args()
    build_pages_data(Path(args.data_dir), Path(args.site_data_dir))


if __name__ == "__main__":
    main()
