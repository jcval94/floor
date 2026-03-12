from __future__ import annotations

import json
from pathlib import Path


def _last_jsonl_row(path: Path) -> dict | None:
    if not path.exists():
        return None
    last = None
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                last = json.loads(line)
    return last


def build_dashboard_snapshot(data_dir: Path, output_path: Path) -> None:
    pred_files = sorted((data_dir / "predictions").glob("*.jsonl"))
    signal_files = sorted((data_dir / "signals").glob("*.jsonl"))

    payload = {
        "prediction_files": len(pred_files),
        "signal_files": len(signal_files),
        "latest_predictions": [],
    }

    for f in pred_files[:20]:
        row = _last_jsonl_row(f)
        if row:
            payload["latest_predictions"].append(row)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
