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
                "momentum_20": 0.02,
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

    # This fixture represents a healthy serving champion. Individual tests can
    # override these frozen OOS quality metrics to exercise fail-closed review.
    timing_path = training_dir / "models" / "timing_champion.json"
    timing_payload = json.loads(timing_path.read_text(encoding="utf-8"))
    timing_payload["metrics"].update(
        {
            "quality_log_loss": 2.40,
            "uniform_log_loss": 2.56,
            "log_loss_skill": 0.0625,
        }
    )
    timing_path.write_text(
        json.dumps(timing_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
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
    assert summary["tasks_for_retrain_recommended"] == []
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
    assert summary["tasks_for_retrain_recommended"] == ["value"]
    assert summary["models"]["value"]["recommendation"] == "RETRAIN_NOW"
    assert summary["models"]["timing"]["recommendation"] == "SKIP_RETRAIN"


def test_schema_review_ignores_irrelevant_dataset_columns(tmp_path: Path) -> None:
    data_dir = _setup_training(tmp_path)
    dataset_path = data_dir / "training" / "modelable_dataset.json"
    payload = json.loads(dataset_path.read_text(encoding="utf-8"))
    for row in payload["rows"]:
        row["new_irrelevant_debug_column"] = None
    dataset_path.write_text(json.dumps(payload), encoding="utf-8")

    summary = run_training_review(
        data_dir=data_dir,
        output_path=data_dir / "training" / "reviews.jsonl",
        summary_path=data_dir / "training" / "review_summary_latest.json",
        config_path=Path("config/retraining.yaml"),
    )

    for model in summary["models"].values():
        schema = model["summary"]["schema"]
        assert schema["state"] == "GREEN"
        assert "new_irrelevant_debug_column" in schema["ignored_added_columns"]


def test_schema_review_fails_closed_when_required_input_disappears(tmp_path: Path) -> None:
    data_dir = _setup_training(tmp_path)
    dataset_path = data_dir / "training" / "modelable_dataset.json"
    payload = json.loads(dataset_path.read_text(encoding="utf-8"))
    for row in payload["rows"]:
        row.pop("close", None)
    dataset_path.write_text(json.dumps(payload), encoding="utf-8")

    summary = run_training_review(
        data_dir=data_dir,
        output_path=data_dir / "training" / "reviews.jsonl",
        summary_path=data_dir / "training" / "review_summary_latest.json",
        config_path=Path("config/retraining.yaml"),
    )

    assert summary["models"]["value"]["summary"]["schema"]["state"] == "RED"
    assert "close" in summary["models"]["value"]["summary"]["schema"]["removed_columns"]


def test_unused_config_feature_shift_does_not_trigger_model_drift(tmp_path: Path) -> None:
    data_dir = _setup_training(tmp_path)
    dataset_path = data_dir / "training" / "modelable_dataset.json"
    payload = json.loads(dataset_path.read_text(encoding="utf-8"))
    for row in payload["rows"]:
        row["ai_consensus_score"] = 999.0
    dataset_path.write_text(json.dumps(payload), encoding="utf-8")

    summary = run_training_review(
        data_dir=data_dir,
        output_path=data_dir / "training" / "reviews.jsonl",
        summary_path=data_dir / "training" / "review_summary_latest.json",
        config_path=Path("config/retraining.yaml"),
    )

    for model in summary["models"].values():
        assert "ai_consensus_score" not in model["summary"]["shared_data"]["features"]

def test_timing_serving_quality_block_forces_retrain_now(tmp_path: Path) -> None:
    data_dir = _setup_training(tmp_path)
    timing_path = data_dir / "training" / "models" / "timing_champion.json"
    timing_payload = json.loads(timing_path.read_text(encoding="utf-8"))
    timing_payload["metrics"].update(
        {
            "quality_log_loss": 2.60,
            "uniform_log_loss": 2.56,
            "log_loss_skill": -0.01,
        }
    )
    timing_path.write_text(
        json.dumps(timing_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    summary = run_training_review(
        data_dir=data_dir,
        output_path=data_dir / "training" / "reviews.jsonl",
        summary_path=data_dir / "training" / "review_summary_latest.json",
        config_path=Path("config/retraining.yaml"),
    )

    timing = summary["models"]["timing"]
    assert timing["summary"]["performance"]["state"] == "RED"
    assert timing["summary"]["performance"]["deltas"]["serving_quality_blocked"] == 1.0
    assert timing["recommendation"] == "RETRAIN_NOW"
    assert "timing" in summary["tasks_for_auto_retrain"]



def test_value_review_uses_scale_free_pinball_for_schema_v2_champion(
    tmp_path: Path,
) -> None:
    data_dir = _setup_training(tmp_path)
    dataset_path = data_dir / "training" / "modelable_dataset.json"
    payload = json.loads(dataset_path.read_text(encoding="utf-8"))

    # Preserve the relative floor problem while moving the whole validation
    # price level 10x higher. Raw-dollar pinball must not become a retrain-now
    # signal for a model whose target contract is relative_floor_delta.
    for row in payload["rows"]:
        if row.get("split") != "validation":
            continue
        for field in ("close", "atr_14", "floor_m3", "realized_floor_m3"):
            row[field] = float(row[field]) * 10.0

    dataset_path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )

    summary = run_training_review(
        data_dir=data_dir,
        output_path=data_dir / "training" / "reviews.jsonl",
        summary_path=data_dir / "training" / "review_summary_latest.json",
        config_path=Path("config/retraining.yaml"),
    )

    performance = summary["models"]["value"]["summary"]["performance"]
    assert performance["diagnostics"]["metric_contract"] == "relative_floor_delta"
    assert (
        performance["current_metrics"]["pinball_loss"]
        > performance["baseline_metrics"]["pinball_loss"] * 5.0
    )
    assert performance["state"] != "RED"
    assert "pinball_loss_delta" in performance["current_metrics"]


def test_m3_review_excludes_split_ineligible_rows_from_performance(
    tmp_path: Path,
) -> None:
    data_dir = _setup_training(tmp_path)
    dataset_path = data_dir / "training" / "modelable_dataset.json"
    payload = json.loads(dataset_path.read_text(encoding="utf-8"))

    validation_rows = [
        row for row in payload["rows"] if row.get("split") == "validation"
    ]
    assert validation_rows
    validation_rows[0]["split_eligible_m3"] = False
    expected = len(validation_rows) - 1

    dataset_path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )

    summary = run_training_review(
        data_dir=data_dir,
        output_path=data_dir / "training" / "reviews.jsonl",
        summary_path=data_dir / "training" / "review_summary_latest.json",
        config_path=Path("config/retraining.yaml"),
    )

    value_metrics = summary["models"]["value"]["summary"]["performance"]["current_metrics"]
    timing_metrics = summary["models"]["timing"]["summary"]["performance"]["current_metrics"]
    assert value_metrics["evaluation_rows"] == expected
    assert timing_metrics["evaluation_rows"] == expected
