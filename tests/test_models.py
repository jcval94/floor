from __future__ import annotations

import json
from pathlib import Path

from models.evaluate import top3_weeks
from models.run_training import run_training
from models.train_timing_models import train_floor_week_m3_timing_model
from models.train_value_models import train_floor_m3_value_model


def _rows(n: int = 60) -> list[dict]:
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


def test_train_value_and_timing_are_separate() -> None:
    rows = _rows()
    train = [r for r in rows if r["split"] == "train"]
    valid = [r for r in rows if r["split"] == "validation"]

    value = train_floor_m3_value_model(train, valid, model_name="value", version="vtest")
    timing = train_floor_week_m3_timing_model(train, valid, model_name="timing", version="vtest")

    assert value.target == "floor_m3"
    assert timing.target == "floor_week_m3"
    assert value.horizon == timing.horizon == "m3"


def test_timing_outputs_probabilities_best_and_top3() -> None:
    rows = _rows()
    train = [r for r in rows if r["split"] == "train"]
    valid = [r for r in rows if r["split"] == "validation"]

    timing = train_floor_week_m3_timing_model(train, valid, model_name="timing", version="vtest")
    assert timing.probabilities
    assert len(timing.probabilities[0]) == 13
    assert abs(sum(timing.probabilities[0]) - 1.0) < 1e-9
    assert 1 <= timing.best_class[0] <= 13
    assert len(timing.top3[0]) == 3
    assert all(1 <= d["week"] <= 13 for d in timing.top3[0])


def test_metrics_contracts_exist() -> None:
    rows = _rows()
    train = [r for r in rows if r["split"] == "train"]
    valid = [r for r in rows if r["split"] == "validation"]

    value = train_floor_m3_value_model(train, valid, model_name="value", version="vtest")
    timing = train_floor_week_m3_timing_model(train, valid, model_name="timing", version="vtest")

    for metric in ["pinball_loss", "mae_realized_floor", "breach_rate", "calibration_error", "temporal_stability"]:
        assert metric in value.metrics
    for metric in [
        "top1_accuracy",
        "top3_accuracy",
        "log_loss",
        "brier_score",
        "expected_week_distance",
        "confusion_matrix",
        "calibration_error",
    ]:
        assert metric in timing.metrics


def test_run_training_persists_artifacts_and_snapshot(tmp_path: Path) -> None:
    dataset = {"rows": _rows(80)}
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text(json.dumps(dataset, ensure_ascii=False), encoding="utf-8")

    out = tmp_path / "training"
    first = run_training(dataset_path, out, version="v1")
    second = run_training(dataset_path, out, version="v2")

    assert Path(first["metrics_path"]).exists()
    assert Path(second["metrics_path"]).exists()
    assert first["tasks"] == ["value", "timing"]

    value_champ = out / "models" / "value_champion.json"
    timing_champ = out / "models" / "timing_champion.json"
    assert value_champ.exists()
    assert timing_champ.exists()

    challengers = list((out / "models").glob("*_challenger_*.json"))
    assert len(challengers) >= 2

    champ_payload = json.loads(value_champ.read_text(encoding="utf-8"))
    assert "selection" in champ_payload
    assert "reason" in champ_payload["selection"]
    assert "dataset_summary" in champ_payload

    metrics_payload = json.loads(Path(second["metrics_path"]).read_text(encoding="utf-8"))
    assert metrics_payload["tasks"] == ["value", "timing"]
    assert "dataset_summary" in metrics_payload


def test_run_training_single_task_keeps_other_champion(tmp_path: Path) -> None:
    dataset = {"rows": _rows(80)}
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text(json.dumps(dataset, ensure_ascii=False), encoding="utf-8")

    out = tmp_path / "training"
    run_training(dataset_path, out, version="v1", tasks="value,timing")
    timing_before = (out / "models" / "timing_champion.json").read_text(encoding="utf-8")

    result = run_training(dataset_path, out, version="v2", tasks="value")

    assert result["tasks"] == ["value"]
    assert "timing" not in result
    assert (out / "models" / "value_champion.json").exists()
    assert (out / "models" / "timing_champion.json").read_text(encoding="utf-8") == timing_before


def test_forecast_contract_top3_helper() -> None:
    probs = [0.01] * 13
    probs[2] = 0.4
    probs[7] = 0.2
    probs[11] = 0.15
    total = sum(probs)
    probs = [p / total for p in probs]

    top3 = top3_weeks(probs)
    assert [x["week"] for x in top3] == [3, 8, 12]
