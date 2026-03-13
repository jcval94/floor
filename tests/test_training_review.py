from __future__ import annotations

import json
from pathlib import Path

from floor.training.review import run_training_review
from models.run_training import run_training


def _rows(n: int = 80) -> list[dict]:
    rows: list[dict] = []
    for i in range(n):
        split = "train" if i < int(0.7 * n) else "validation"
        rows.append(
            {
                "split": split,
                "close": 100.0 + i,
                "atr_14": 1.0 + 0.01 * i,
                "trend_context_m3": 0.05,
                "drawdown_13w": -0.03,
                "dist_to_low_3m": 0.08,
                "ai_conviction_long": 0.7,
                "ai_horizon_alignment": 1.0,
                "ai_recency_long": 2.0,
                "floor_m3": 95.0 + 0.02 * i,
                "realized_floor_m3": 94.5 + 0.02 * i,
                "floor_week_m3": (i % 13) + 1,
            }
        )
    return rows


def _setup_training(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    training_dir = data_dir / "training"
    training_dir.mkdir(parents=True, exist_ok=True)
    dataset_path = training_dir / "modelable_dataset.json"
    dataset_path.write_text(json.dumps({"rows": _rows()}, ensure_ascii=False), encoding="utf-8")
    run_training(dataset_path, training_dir, version="v1", tasks="value,timing")
    return data_dir


def test_run_training_review_writes_summary_and_history(tmp_path: Path) -> None:
    data_dir = _setup_training(tmp_path)

    summary = run_training_review(
        data_dir=data_dir,
        output_path=data_dir / "training" / "reviews.jsonl",
        summary_path=data_dir / "training" / "review_summary_latest.json",
        config_path=Path("config/retraining.yaml"),
    )

    assert summary["tasks_for_auto_retrain"] == []
    assert summary["models"]["value"]["recommendation"] == "SKIP_RETRAIN"
    assert summary["models"]["timing"]["recommendation"] == "SKIP_RETRAIN"

    lines = (data_dir / "training" / "reviews.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    latest = json.loads((data_dir / "training" / "review_summary_latest.json").read_text(encoding="utf-8"))
    assert set(latest["models"].keys()) == {"value", "timing"}


def test_run_training_review_marks_only_value_for_auto_retrain(tmp_path: Path) -> None:
    data_dir = _setup_training(tmp_path)
    value_path = data_dir / "training" / "models" / "value_champion.json"
    value_payload = json.loads(value_path.read_text(encoding="utf-8"))
    value_payload["dataset_summary"]["numeric_stats"]["floor_m3"]["mean"] = 1.0
    value_payload["dataset_summary"]["numeric_stats"]["realized_floor_m3"]["mean"] = 1.0
    value_path.write_text(json.dumps(value_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = run_training_review(
        data_dir=data_dir,
        output_path=data_dir / "training" / "reviews.jsonl",
        summary_path=data_dir / "training" / "review_summary_latest.json",
        config_path=Path("config/retraining.yaml"),
    )

    assert summary["tasks_for_auto_retrain"] == ["value"]
    assert summary["models"]["value"]["recommendation"] == "RETRAIN_NOW"
    assert summary["models"]["timing"]["recommendation"] == "SKIP_RETRAIN"
