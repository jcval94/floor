from __future__ import annotations

import csv
import json
from pathlib import Path


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
    return {k: v for k, v in record.items() if k in _ALLOWED_KEYS}


def _write_json_csv(base: Path, name: str, rows: list[dict]) -> None:
    base.mkdir(parents=True, exist_ok=True)
    latest_json = base / f"{name}_latest.json"
    latest_csv = base / f"{name}_latest.csv"

    clean_rows = [_sanitize_record(r) for r in rows]
    latest_json.write_text(json.dumps(clean_rows, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")

    fieldnames = sorted({k for r in clean_rows for k in r.keys()})
    with latest_csv.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for r in clean_rows:
            writer.writerow(r)


def export_pages_data(output_dir: str, date_partition: str, datasets: dict[str, list[dict]]) -> dict:
    root = Path(output_dir)
    historical = root / "historical" / date_partition
    latest = root / "latest"

    exported = {}
    for dataset_name in [
        "dashboard_overview",
        "ticker_detail",
        "model_health",
        "strategy_performance",
        "retrain_history",
        "incident_log",
    ]:
        rows = list(datasets.get(dataset_name, []))
        _write_json_csv(latest, dataset_name, rows)
        _write_json_csv(historical, dataset_name, rows)
        exported[dataset_name] = {
            "latest_json": str(latest / f"{dataset_name}_latest.json"),
            "latest_csv": str(latest / f"{dataset_name}_latest.csv"),
            "historical_json": str(historical / f"{dataset_name}_latest.json"),
            "historical_csv": str(historical / f"{dataset_name}_latest.csv"),
        }

    return {"output_dir": str(root), "date_partition": date_partition, "datasets": exported}
