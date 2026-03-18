from __future__ import annotations

import json
import hashlib
import pickle
from pathlib import Path

from floor.persistence_db import stream_count
from models.evaluate import top3_weeks
from models.run_training import run_training
from models.train_timing_models import TimingModelArtifact, train_floor_week_m3_timing_model
from models.train_value_models import ValueModelArtifact, train_floor_m3_value_model


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
    assert first["tasks"] == ["d1", "w1", "q1", "value", "timing"]

    value_champ = out / "models" / "value_champion.json"
    timing_champ = out / "models" / "timing_champion.json"
    d1_champ = out / "models" / "d1_champion.json"
    w1_champ = out / "models" / "w1_champion.json"
    q1_champ = out / "models" / "q1_champion.json"
    assert value_champ.exists()
    assert timing_champ.exists()
    assert d1_champ.exists()
    assert w1_champ.exists()
    assert q1_champ.exists()

    challengers = list((out / "models").glob("*_challenger_*.json"))
    assert len(challengers) >= 2

    champ_payload = json.loads(value_champ.read_text(encoding="utf-8"))
    assert "selection" in champ_payload
    assert "reason" in champ_payload["selection"]
    assert "dataset_summary" in champ_payload

    metrics_payload = json.loads(Path(second["metrics_path"]).read_text(encoding="utf-8"))
    assert metrics_payload["tasks"] == ["d1", "w1", "q1", "value", "timing"]
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


def test_run_training_retrain_enables_cv_and_audit(tmp_path: Path) -> None:
    dataset = {"rows": _rows(120)}
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text(json.dumps(dataset, ensure_ascii=False), encoding="utf-8")

    out = tmp_path / "training"
    db_path = tmp_path / "persistence" / "app.sqlite"
    result = run_training(dataset_path, out, version="v-cv", tasks="value,timing", training_mode="retrain", persistence_db_path=db_path)

    assert result["training_mode"] == "retrain"
    value_champ = json.loads((out / "models" / "value_champion.json").read_text(encoding="utf-8"))
    timing_champ = json.loads((out / "models" / "timing_champion.json").read_text(encoding="utf-8"))

    assert value_champ["params"]["tuning_summary"]["cv_enabled"] is True
    assert timing_champ["params"]["tuning_summary"]["cv_enabled"] is True
    assert stream_count(db_path, "model_training_cycles") == 5


def test_run_training_retrain_persists_only_winners_in_models_file(tmp_path: Path) -> None:
    dataset = {"rows": _rows(90)}
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text(json.dumps(dataset, ensure_ascii=False), encoding="utf-8")

    out = tmp_path / "training"
    standard = run_training(dataset_path, out, version="v1", tasks="value,timing", training_mode="standard")
    assert standard["training_mode"] == "standard"
    for task in ("d1", "w1", "q1", "value", "timing"):
        assert not (out / "models_file" / f"{task}_champion.pkl").exists()
        assert not (out / "models_file" / f"{task}_champion.manifest.json").exists()

    retrain = run_training(dataset_path, out, version="v2", tasks="value,timing", training_mode="retrain")
    assert retrain["training_mode"] == "retrain"

    d1_pkl = out / "models_file" / "d1_champion.pkl"
    w1_pkl = out / "models_file" / "w1_champion.pkl"
    q1_pkl = out / "models_file" / "q1_champion.pkl"
    value_pkl = out / "models_file" / "value_champion.pkl"
    timing_pkl = out / "models_file" / "timing_champion.pkl"

    for pkl in (d1_pkl, w1_pkl, q1_pkl):
        assert not pkl.exists()

    if retrain["value"]["decision"] in {"promote", "promote_first"}:
        assert value_pkl.exists()
        with value_pkl.open("rb") as fh:
            value_payload = pickle.load(fh)
        value_manifest = json.loads((out / "models_file" / "value_champion.manifest.json").read_text(encoding="utf-8"))
        expected_hash = hashlib.sha256(value_pkl.read_bytes()).hexdigest()
        assert value_payload["selection"]["objective"] == "minimize_weighted_error"
        assert isinstance(value_payload["selection"]["new_score"], float)
        assert value_manifest["task"] == "value"
        assert value_manifest["sha256"] == expected_hash

    if retrain["timing"]["decision"] in {"promote", "promote_first"}:
        assert timing_pkl.exists()
        with timing_pkl.open("rb") as fh:
            timing_payload = pickle.load(fh)
        timing_manifest = json.loads((out / "models_file" / "timing_champion.manifest.json").read_text(encoding="utf-8"))
        expected_hash = hashlib.sha256(timing_pkl.read_bytes()).hexdigest()
        assert timing_payload["selection"]["objective"] == "minimize_weighted_error"
        assert isinstance(timing_payload["selection"]["new_score"], float)
        assert timing_manifest["task"] == "timing"
        assert timing_manifest["sha256"] == expected_hash
    else:
        assert not timing_pkl.exists()
        assert not (out / "models_file" / "timing_champion.manifest.json").exists()


def test_run_training_persists_large_champion_payloads_in_repo_json(tmp_path: Path, monkeypatch) -> None:
    dataset = {"rows": _rows(80)}
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text(json.dumps(dataset, ensure_ascii=False), encoding="utf-8")

    large_weights = {f"feature_{i}": float(i) * 0.0001 for i in range(120_000)}

    def _fake_value(*_args, **_kwargs) -> ValueModelArtifact:
        return ValueModelArtifact(
            model_name="m3_value_linear",
            horizon="m3",
            target="floor_m3",
            version="v-large",
            params={"weights": large_weights, "bias": 95.0},
            metrics={
                "pinball_loss": 0.1,
                "mae_realized_floor": 0.1,
                "breach_rate": 0.2,
                "calibration_error": 0.1,
                "temporal_stability": 0.9,
            },
            predictions=[95.0],
            confidences=[0.6],
        )

    def _fake_timing(*_args, **_kwargs) -> TimingModelArtifact:
        probs = [[1 / 13] * 13]
        return TimingModelArtifact(
            model_name="m3_timing_multiclass",
            horizon="m3",
            target="floor_week_m3",
            version="t-large",
            params={"calibrator_reliability": {}},
            metrics={
                "top1_accuracy": 0.2,
                "top3_accuracy": 0.4,
                "log_loss": 0.5,
                "brier_score": 0.2,
                "expected_week_distance": 2.0,
                "calibration_error": 0.1,
                "confusion_matrix": {},
            },
            probabilities=probs,
            best_class=[7],
            top3=[[{"week": 7, "prob": 0.2}, {"week": 6, "prob": 0.15}, {"week": 8, "prob": 0.15}]],
        )

    monkeypatch.setattr("models.run_training.train_floor_m3_value_model", _fake_value)
    monkeypatch.setattr("models.run_training.train_floor_week_m3_timing_model", _fake_timing)

    out = tmp_path / "training"
    run_training(dataset_path, out, version="v-large", tasks="value,timing", training_mode="standard")

    value_champ = out / "models" / "value_champion.json"
    assert value_champ.exists()
    assert value_champ.stat().st_size > 1_000_000

    payload = json.loads(value_champ.read_text(encoding="utf-8"))
    assert payload["version"] == "v-large"
    assert len(payload["params"]["weights"]) == 120_000
