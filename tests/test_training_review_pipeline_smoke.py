from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _pythonpath_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    src_path = str(REPO_ROOT / "src")
    current = env.get("PYTHONPATH")
    env["PYTHONPATH"] = src_path if not current else f"{src_path}{os.pathsep}{current}"
    if extra:
        env.update(extra)
    return env


def _dataset_rows() -> list[dict]:
    return [
        {
            "split": "train",
            "close": 100.0,
            "atr_14": 1.0,
            "trend_context_m3": 0.1,
            "drawdown_13w": -0.03,
            "dist_to_low_3m": 0.08,
            "ai_conviction_long": 0.7,
            "ai_horizon_alignment": 1.0,
            "ai_recency_long": 2.0,
            "floor_m3": 95.0,
            "realized_floor_m3": 94.5,
            "floor_week_m3": 4,
        },
        {
            "split": "validation",
            "close": 101.0,
            "atr_14": 1.1,
            "trend_context_m3": 0.1,
            "drawdown_13w": -0.02,
            "dist_to_low_3m": 0.09,
            "ai_conviction_long": 0.72,
            "ai_horizon_alignment": 1.0,
            "ai_recency_long": 2.0,
            "floor_m3": 95.1,
            "realized_floor_m3": 94.6,
            "floor_week_m3": 5,
        },
    ]


def test_floor_training_run_retrain_assessment_import_backcompat() -> None:
    __import__("floor.training.run_retrain_assessment")


def test_training_and_review_cli_smoke_generates_summary(tmp_path: Path) -> None:
    root_dir = tmp_path / "root"
    data_dir = tmp_path / "data"
    (root_dir / "config").mkdir(parents=True, exist_ok=True)
    (data_dir / "training").mkdir(parents=True, exist_ok=True)

    retraining_cfg = (REPO_ROOT / "config" / "retraining.yaml").read_text(encoding="utf-8")
    (root_dir / "config" / "retraining.yaml").write_text(retraining_cfg, encoding="utf-8")

    dataset_path = data_dir / "training" / "modelable_dataset.json"
    dataset_path.write_text(json.dumps({"rows": _dataset_rows()}, ensure_ascii=False), encoding="utf-8")

    train = subprocess.run(
        [
            sys.executable,
            "-m",
            "models.run_training",
            "--dataset",
            str(dataset_path),
            "--output-dir",
            str(data_dir / "training"),
            "--tasks",
            "value,timing",
        ],
        cwd=REPO_ROOT,
        env=_pythonpath_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert train.returncode == 0, train.stderr or train.stdout

    review = subprocess.run(
        [sys.executable, "-m", "floor.main", "review-training"],
        cwd=REPO_ROOT,
        env=_pythonpath_env(
            {
                "FLOOR_ROOT_DIR": str(root_dir),
                "FLOOR_DATA_DIR": str(data_dir),
            }
        ),
        capture_output=True,
        text=True,
        check=False,
    )
    assert review.returncode == 0, review.stderr or review.stdout

    summary_path = data_dir / "training" / "review_summary_latest.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert set(summary["models"].keys()) == {"value", "timing"}
    assert "suite_recommendation" in summary
