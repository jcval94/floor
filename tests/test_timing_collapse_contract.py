from __future__ import annotations

import json
import math
from pathlib import Path

from models.evaluate import timing_metrics, timing_serving_quality_blocked
from models.select_champion import select_and_persist_champion


def _valid_metrics() -> dict:
    uniform = math.log(13)
    return {
        "quality_top1_accuracy": 0.20,
        "quality_top3_accuracy": 0.45,
        "quality_log_loss": 2.40,
        "quality_brier_score": 0.07,
        "quality_expected_week_distance": 3.5,
        "quality_calibration_error": 0.03,
        "quality_top1_unique_classes": 4,
        "quality_top1_dominant_share": 0.45,
        "uniform_log_loss": uniform,
        "log_loss_skill": 1.0 - (2.40 / uniform),
        "abstention_rate": 0.0,
        "validation_rows": 200,
    }


def _artifact(version: str, metrics: dict) -> dict:
    return {
        "model_name": "m3_timing_multiclass",
        "version": version,
        "params": {
            "schema_version": 2,
            "model_type": "multinomial_logistic",
            "class_count": 13,
        },
        "metrics": metrics,
    }


def test_timing_metrics_exposes_top1_collapse() -> None:
    y_true = list(range(1, 14)) * 3
    probs = [[0.30] + [0.70 / 12.0] * 12 for _ in y_true]

    metrics = timing_metrics(y_true, probs)

    assert metrics["top1_unique_classes"] == 1
    assert metrics["top1_dominant_share"] == 1.0


def test_serving_blocks_legacy_champion_when_confusion_matrix_is_collapsed() -> None:
    metrics = {
        "quality_log_loss": 2.40,
        "uniform_log_loss": math.log(13),
        "log_loss_skill": 0.05,
        "confusion_matrix": {
            str(actual): {str(pred): (10 if pred == 1 else 0) for pred in range(1, 14)}
            for actual in range(1, 14)
        },
    }

    assert timing_serving_quality_blocked(metrics) is True


def test_collapsed_timing_challenger_cannot_replace_valid_champion(tmp_path: Path) -> None:
    registry = tmp_path / "models"
    registry.mkdir(parents=True)
    (registry / "timing_champion.json").write_text(
        json.dumps(_artifact("good-v1", _valid_metrics())),
        encoding="utf-8",
    )

    collapsed = _valid_metrics()
    collapsed["quality_top1_unique_classes"] = 1
    collapsed["quality_top1_dominant_share"] = 1.0

    result = select_and_persist_champion(
        _artifact("bad-v2", collapsed),
        registry,
        task="timing",
    )

    assert result["decision"] == "challenger_only"
    champion = json.loads((registry / "timing_champion.json").read_text(encoding="utf-8"))
    assert champion["version"] == "good-v1"


def test_valid_timing_challenger_can_replace_legacy_quality_schema(tmp_path: Path) -> None:
    registry = tmp_path / "models"
    registry.mkdir(parents=True)
    legacy = _valid_metrics()
    legacy.pop("quality_top1_unique_classes")
    legacy.pop("quality_top1_dominant_share")
    (registry / "timing_champion.json").write_text(
        json.dumps(_artifact("legacy-v1", legacy)),
        encoding="utf-8",
    )

    result = select_and_persist_champion(
        _artifact("good-v2", _valid_metrics()),
        registry,
        task="timing",
    )

    assert result["decision"] == "promote"
    champion = json.loads((registry / "timing_champion.json").read_text(encoding="utf-8"))
    assert champion["version"] == "good-v2"
