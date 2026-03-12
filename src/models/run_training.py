from __future__ import annotations

import argparse
import json
from pathlib import Path

from models.select_champion import select_and_persist_champion
from models.train_timing_models import train_floor_week_m3_timing_model
from models.train_value_models import train_floor_m3_value_model


def _load_dataset(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "rows" in payload:
        return payload["rows"]
    if isinstance(payload, list):
        return payload
    raise ValueError("Unsupported dataset payload")


def _split_rows(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    train = [r for r in rows if r.get("split") == "train"]
    valid = [r for r in rows if r.get("split") in {"validation", "test"}]
    if not train:
        train = rows[: int(len(rows) * 0.7)]
    if not valid:
        valid = rows[int(len(rows) * 0.7) :]
    return train, valid


def run_training(dataset_path: Path, output_dir: Path, version: str = "v1") -> dict:
    rows = _load_dataset(dataset_path)
    train, valid = _split_rows(rows)

    value_artifact = train_floor_m3_value_model(train, valid, model_name="m3_value_linear", version=version)
    timing_artifact = train_floor_week_m3_timing_model(train, valid, model_name="m3_timing_multiclass", version=version)

    models_dir = output_dir / "models"
    metrics_dir = output_dir / "metrics"
    models_dir.mkdir(parents=True, exist_ok=True)
    metrics_dir.mkdir(parents=True, exist_ok=True)

    value_sel = select_and_persist_champion(value_artifact, models_dir, task="value")
    timing_sel = select_and_persist_champion(timing_artifact, models_dir, task="timing")

    (metrics_dir / f"training_metrics_{version}.json").write_text(
        json.dumps(
            {
                "value": value_artifact.metrics,
                "timing": timing_artifact.metrics,
                "selection": {"value": value_sel, "timing": timing_sel},
                "forecast_contract": {
                    "floor_week_m3_best_class": timing_artifact.best_class,
                    "floor_week_m3_top3": timing_artifact.top3,
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    return {
        "value": value_sel,
        "timing": timing_sel,
        "metrics_path": str(metrics_dir / f"training_metrics_{version}.json"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Train m3 value and timing models with champion/challenger selection")
    parser.add_argument("--dataset", required=True, help="Path to modelable dataset JSON")
    parser.add_argument("--output-dir", default="data/training", help="Output directory for artifacts and metrics")
    parser.add_argument("--version", default="v1", help="Training version tag")
    args = parser.parse_args()

    result = run_training(Path(args.dataset), Path(args.output_dir), version=args.version)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
