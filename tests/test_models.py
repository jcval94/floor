from __future__ import annotations

import hashlib
import json
import pickle
from pathlib import Path

import pytest

from floor.persistence_db import stream_count
from models.evaluate import top3_weeks
from models.run_training import run_training
from models.train_timing_models import train_floor_week_m3_timing_model
from models.train_value_models import train_floor_m3_value_model


def _rows(n: int = 90) -> list[dict]:
    rows: list[dict] = []
    for i in range(n):
        split = "train" if i < int(0.7 * n) else "validation"
        close = 100.0 + i
        floor = close * (1.0 - (0.04 + (i % 5) * 0.002))
        rows.append(
            {
                "split": split,
                "split_eligible_m3": True,
                "close": close,
                "atr_14": 1.0 + 0.01 * i,
                "trend_context_m3": 0.05 if i % 2 == 0 else -0.04,
                "drawdown_13w": -0.03,
                "dist_to_low_3m": 0.08,
                "momentum_20": 0.02 if i % 3 else -0.01,
                "ai_conviction_long": 0.0,
                "ai_horizon_alignment": 0.0,
                "ai_recency_long": None,
                "floor_m3": floor,
                "floor_delta_m3": (close - floor) / close,
                "realized_floor_m3": floor,
                "floor_week_m3": (i % 13) + 1,
            }
        )
    return rows


def test_train_value_and_timing_are_separate() -> None:
    rows = _rows()
    train = [row for row in rows if row["split"] == "train"]
    valid = [row for row in rows if row["split"] == "validation"]
    value = train_floor_m3_value_model(train, valid, model_name="value", version="vtest")
    timing = train_floor_week_m3_timing_model(train, valid, model_name="timing", version="vtest")
    assert value.target == "floor_m3"
    assert timing.target == "floor_week_m3"
    assert value.horizon == timing.horizon == "m3"


def test_timing_outputs_probabilities_best_and_top3() -> None:
    rows = _rows()
    train = [row for row in rows if row["split"] == "train"]
    valid = [row for row in rows if row["split"] == "validation"]
    timing = train_floor_week_m3_timing_model(train, valid, model_name="timing", version="vtest")
    assert timing.probabilities
    assert len(timing.probabilities[0]) == 13
    assert abs(sum(timing.probabilities[0]) - 1.0) < 1e-9
    assert 1 <= timing.best_class[0] <= 13
    assert len(timing.top3[0]) == 3


def test_metrics_contracts_exist() -> None:
    rows = _rows()
    train = [row for row in rows if row["split"] == "train"]
    valid = [row for row in rows if row["split"] == "validation"]
    value = train_floor_m3_value_model(train, valid, model_name="value", version="vtest")
    timing = train_floor_week_m3_timing_model(train, valid, model_name="timing", version="vtest")
    for metric in ["pinball_loss", "mae_realized_floor", "breach_rate", "calibration_error", "temporal_stability"]:
        assert metric in value.metrics
    for metric in ["top1_accuracy", "top3_accuracy", "log_loss", "brier_score", "expected_week_distance", "confusion_matrix", "calibration_error"]:
        assert metric in timing.metrics


def test_run_training_defaults_to_m3_only(tmp_path: Path) -> None:
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text(json.dumps({"rows": _rows()}), encoding="utf-8")
    out = tmp_path / "training"
    result = run_training(dataset_path, out, version="v1")
    assert result["tasks"] == ["value", "timing"]
    assert (out / "models" / "value_champion.json").exists()
    assert (out / "models" / "timing_champion.json").exists()
    for task in ("d1", "w1", "q1"):
        assert not (out / "models" / f"{task}_champion.json").exists()
    metrics = json.loads(Path(result["metrics_path"]).read_text(encoding="utf-8"))
    assert metrics["test_holdout_used_for_selection"] is False


def test_run_training_rejects_duplicate_horizon_trainer_path(tmp_path: Path) -> None:
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text(json.dumps({"rows": _rows()}), encoding="utf-8")
    with pytest.raises(ValueError, match="train_classic_horizons"):
        run_training(dataset_path, tmp_path / "training", tasks="d1,value")


def test_run_training_single_task_keeps_other_champion(tmp_path: Path) -> None:
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text(json.dumps({"rows": _rows()}), encoding="utf-8")
    out = tmp_path / "training"
    run_training(dataset_path, out, version="v1", tasks="value,timing")
    timing_before = (out / "models" / "timing_champion.json").read_text(encoding="utf-8")
    result = run_training(dataset_path, out, version="v2", tasks="value")
    assert result["tasks"] == ["value"]
    assert (out / "models" / "timing_champion.json").read_text(encoding="utf-8") == timing_before


def test_forecast_contract_top3_helper() -> None:
    probs = [0.01] * 13
    probs[2] = 0.4
    probs[7] = 0.2
    probs[11] = 0.15
    total = sum(probs)
    top3 = top3_weeks([prob / total for prob in probs])
    assert [item["week"] for item in top3] == [3, 8, 12]


def test_run_training_retrain_audits_only_m3_tasks(tmp_path: Path) -> None:
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text(json.dumps({"rows": _rows(120)}), encoding="utf-8")
    out = tmp_path / "training"
    db_path = tmp_path / "persistence" / "app.sqlite"
    result = run_training(
        dataset_path,
        out,
        version="v-cv",
        tasks="value,timing",
        training_mode="retrain",
        persistence_db_path=db_path,
    )
    assert result["training_mode"] == "retrain"
    assert stream_count(db_path, "model_training_cycles") == 2


def test_retrain_models_file_manifest_matches_payload(tmp_path: Path) -> None:
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text(json.dumps({"rows": _rows(100)}), encoding="utf-8")
    out = tmp_path / "training"
    run_training(dataset_path, out, version="v2", tasks="value,timing", training_mode="retrain")
    for task in ("value", "timing"):
        pkl = out / "models_file" / f"{task}_champion.pkl"
        manifest_path = out / "models_file" / f"{task}_champion.manifest.json"
        assert pkl.exists() and manifest_path.exists()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["task"] == task
        assert manifest["sha256"] == hashlib.sha256(pkl.read_bytes()).hexdigest()
        assert manifest["version"] == manifest["model_version"]
        with pkl.open("rb") as fh:
            payload = pickle.load(fh)
        assert payload["version"] == manifest["model_version"]


def test_manual_training_syncs_m3_champions_only(tmp_path: Path) -> None:
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text(json.dumps({"rows": _rows()}), encoding="utf-8")
    out = tmp_path / "training"
    result = run_training(dataset_path, out, version="v-manual", tasks="value,timing", training_mode="manual")
    assert result["tasks"] == ["value", "timing"]
    for task in ("value", "timing"):
        assert (out / "models_file" / f"{task}_champion.pkl").exists()
        assert (out / "models_file" / f"{task}_champion.manifest.json").exists()
    for task in ("d1", "w1", "q1"):
        assert not (out / "models_file" / f"{task}_champion.pkl").exists()
