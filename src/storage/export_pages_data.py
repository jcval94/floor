from __future__ import annotations

import csv
import json
from pathlib import Path


DATASET_NAMES = [
    "dashboard_overview",
    "ticker_detail",
    "model_health",
    "strategy_performance",
    "retrain_history",
    "incident_log",
]

_ALLOWED_KEYS = {
    "date",
    "ticker",
    "metric",
    "value",
    "status",
    "strategy",
    "pnl",
    "drawdown",
    "win_rate",
    "incident",
    "severity",
    "retrain",
    "horizon",
}


def _sanitize_record(record: dict) -> dict:
    return {key: value for key, value in record.items() if key in _ALLOWED_KEYS}


def _write_json_csv(base: Path, dataset_name: str, rows: list[dict]) -> dict:
    base.mkdir(parents=True, exist_ok=True)
    sanitized = [_sanitize_record(row) for row in rows]

    json_path = base / f"{dataset_name}.json"
    csv_path = base / f"{dataset_name}.csv"

    json_path.write_text(json.dumps(sanitized, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")

    fieldnames = sorted({key for row in sanitized for key in row.keys()})
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in sanitized:
            writer.writerow(row)

    return {"json": str(json_path), "csv": str(csv_path)}


def export_pages_data(output_dir: str, date_partition: str, datasets: dict[str, list[dict]]) -> dict:
    root = Path(output_dir)
    latest_root = root / "latest"
    historical_root = root / "historical" / date_partition

    exported: dict[str, dict[str, str]] = {}
    for dataset_name in DATASET_NAMES:
        rows = list(datasets.get(dataset_name, []))

        latest_paths = _write_json_csv(latest_root, dataset_name, rows)
        historical_paths = _write_json_csv(historical_root, dataset_name, rows)

        exported[dataset_name] = {
            "latest_json": latest_paths["json"],
            "latest_csv": latest_paths["csv"],
            "historical_json": historical_paths["json"],
            "historical_csv": historical_paths["csv"],
        }

    return {
        "output_dir": str(root),
        "date_partition": date_partition,
        "datasets": exported,
    }
