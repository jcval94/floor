from __future__ import annotations

import math
from pathlib import Path

import floor.training.review as review
from models.select_champion import select_and_persist_champion


def _timing_artifact(
    version: str,
    *,
    unique_classes: int,
    dominant_share: float,
    log_loss_skill: float = 0.05,
) -> dict:
    uniform = math.log(13)
    return {
        "model_name": "m3_timing_multiclass",
        "version": version,
        "params": {
            "schema_version": 2,
            "model_type": "multinomial_logistic",
            "class_count": 13,
        },
        "metrics": {
            "quality_top1_accuracy": 0.20,
            "quality_top3_accuracy": 0.40,
            "quality_log_loss": uniform * (1.0 - log_loss_skill),
            "quality_brier_score": 0.07,
            "quality_expected_week_distance": 3.0,
            "quality_calibration_error": 0.04,
            "quality_top1_unique_classes": unique_classes,
            "quality_top1_dominant_share": dominant_share,
            "uniform_log_loss": uniform,
            "log_loss_skill": log_loss_skill,
            "abstention_rate": 0.10,
        },
    }


def _review_cfg() -> dict:
    return {
        "performance_thresholds": {
            "pinball_loss_warn": 0.02,
            "pinball_loss_fail": 0.05,
            "breach_rate_warn": 0.03,
            "breach_rate_fail": 0.06,
        },
        "thresholds": {
            "coverage_calibration_warn": 0.03,
            "coverage_calibration_fail": 0.06,
        },
        "m3_performance_thresholds": {
            "top1_accuracy_m3_drop_warn": 0.03,
            "top1_accuracy_m3_drop_fail": 0.06,
            "top3_accuracy_m3_drop_warn": 0.04,
            "top3_accuracy_m3_drop_fail": 0.08,
            "week_distance_m3_warn": 0.4,
            "week_distance_m3_fail": 0.8,
        },
        "timing_thresholds": {
            "log_loss_warn": 0.04,
            "log_loss_fail": 0.08,
            "brier_warn": 0.02,
            "brier_fail": 0.05,
        },
    }


def test_collapsed_timing_challenger_cannot_replace_valid_champion(
    tmp_path: Path,
) -> None:
    registry = tmp_path / "models"
    registry.mkdir(parents=True)
    good = _timing_artifact("good-v1", unique_classes=4, dominant_share=0.45)
    (registry / "timing_champion.json").write_text(
        __import__("json").dumps(good),
        encoding="utf-8",
    )

    result = select_and_persist_champion(
        _timing_artifact("bad-v2", unique_classes=1, dominant_share=1.0),
        registry,
        task="timing",
    )

    assert result["decision"] == "challenger_only"
    champion = __import__("json").loads(
        (registry / "timing_champion.json").read_text(encoding="utf-8")
    )
    assert champion["version"] == "good-v1"


def test_valid_timing_challenger_replaces_invalid_incumbent_even_without_score_comparison(
    tmp_path: Path,
) -> None:
    registry = tmp_path / "models"
    registry.mkdir(parents=True)
    bad = _timing_artifact("bad-v1", unique_classes=1, dominant_share=1.0)
    (registry / "timing_champion.json").write_text(
        __import__("json").dumps(bad),
        encoding="utf-8",
    )

    result = select_and_persist_champion(
        _timing_artifact("good-v2", unique_classes=4, dominant_share=0.45),
        registry,
        task="timing",
    )

    assert result["decision"] == "promote"
    champion = __import__("json").loads(
        (registry / "timing_champion.json").read_text(encoding="utf-8")
    )
    assert champion["version"] == "good-v2"


def test_value_review_uses_scale_free_delta_performance() -> None:
    artifact = {
        "params": {
            "schema_version": 2,
            "target_space": "relative_floor_delta",
            "target_delta_quantile": 0.8,
            "weights": {
                "atr_ratio_14": 0.0,
                "trend_context_m3": 0.0,
                "drawdown_13w": 0.0,
                "dist_to_low_3m": 0.0,
            },
            "bias": 0.15,
            "calibration_scale": 1.0,
        },
        "metrics": {
            "pinball_loss": 2.0,
            "pinball_loss_delta": 0.02,
            "mae_delta": 0.05,
            "breach_rate": 0.2,
            "calibration_error": 0.1,
            "temporal_stability": 1.0,
        },
    }
    rows = [
        {
            "close": 100.0,
            "atr_14": 2.0,
            "trend_context_m3": 0.0,
            "drawdown_13w": -0.1,
            "dist_to_low_3m": 0.2,
            "floor_m3": 80.0,
            "floor_delta_m3": 0.2,
            "ai_conviction_long": 0.0,
        },
        {
            "close": 200.0,
            "atr_14": 4.0,
            "trend_context_m3": 0.0,
            "drawdown_13w": -0.1,
            "dist_to_low_3m": 0.2,
            "floor_m3": 160.0,
            "floor_delta_m3": 0.2,
            "ai_conviction_long": 0.0,
        },
    ]
    scaled = [
        {
            **row,
            "close": row["close"] * 10.0,
            "atr_14": row["atr_14"] * 10.0,
            "floor_m3": row["floor_m3"] * 10.0,
        }
        for row in rows
    ]

    base = review._value_performance(artifact, rows, _review_cfg())
    higher_price = review._value_performance(artifact, scaled, _review_cfg())

    assert base["metric_space"] == "relative_floor_delta"
    assert (
        base["current_metrics"]["pinball_loss_delta"]
        == higher_price["current_metrics"]["pinball_loss_delta"]
    )
    assert (
        higher_price["current_metrics"]["pinball_loss"]
        > base["current_metrics"]["pinball_loss"]
    )


def test_value_target_drift_prefers_relative_training_target() -> None:
    cfg = {
        "m3_drift_thresholds": {
            "realized_floor_m3_js_warn": 0.06,
            "realized_floor_m3_js_fail": 0.12,
        }
    }
    reference = {
        "numeric_stats": {
            "floor_delta_m3": {"mean": 0.2, "std": 0.05},
            "floor_m3": {"mean": 100.0, "std": 10.0},
        }
    }
    current = {
        "numeric_stats": {
            "floor_delta_m3": {"mean": 0.2, "std": 0.05},
            "floor_m3": {"mean": 1000.0, "std": 100.0},
        }
    }

    result = review._value_target_drift(reference, current, cfg)

    assert result["state"] == "GREEN"
    assert result["score"] == 0.0
    assert set(result["targets"]) == {"floor_delta_m3"}


def test_timing_review_flags_collapsed_current_predictions(
    monkeypatch,
) -> None:
    artifact = {
        "metrics": {
            "top1_accuracy": 0.2,
            "top3_accuracy": 0.4,
            "log_loss": 2.4,
            "brier_score": 0.07,
            "expected_week_distance": 3.0,
            "quality_top3_accuracy": 0.4,
            "quality_log_loss": 2.4,
            "uniform_log_loss": math.log(13),
            "log_loss_skill": 0.05,
        }
    }
    monkeypatch.setattr(
        review,
        "predict_timing_week_probabilities",
        lambda row, artifact: [0.3] + [0.7 / 12.0] * 12,
    )
    rows = [{"floor_week_m3": week} for week in range(1, 14)]

    result = review._timing_performance(artifact, rows, _review_cfg())

    assert result["state"] == "RED"
    assert "top1_single_class" in result["absolute_quality"]["current_fail_reasons"]
    assert result["deltas"]["current_prediction_collapse"] == 1.0
