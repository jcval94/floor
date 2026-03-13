from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from floor.schemas import TrainingReviewRecord
from floor.storage import append_jsonl

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
THRESHOLDS = {
    "data_drift": 0.2,
    "concept_drift": 0.15,
    "calibration_drift": 0.1,
    "performance_decay": 0.1,
}


def _load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    except FileNotFoundError:
        logger.warning("[retraining] metrics history file not found path=%s", path)
        return []
    except json.JSONDecodeError as exc:
        logger.exception("[retraining] invalid json in metrics path=%s error=%s", path, exc)
        raise
    logger.info("[retraining] loaded metrics rows=%s path=%s", len(rows), path)
    return rows


def run_training_review(metrics_path: Path, output_path: Path) -> TrainingReviewRecord:
    logger.info("[retraining] start review metrics_path=%s output_path=%s", metrics_path, output_path)
    if not metrics_path.exists():
        record = TrainingReviewRecord(
            as_of=datetime.now(tz=ET),
            model_name="champion-v0",
            data_drift=0.0,
            concept_drift=0.0,
            calibration_drift=0.0,
            performance_decay=0.0,
            thresholds=THRESHOLDS,
            action="SKIP",
            reason="No metrics available; keep champion and collect more evidence.",
        )
        append_jsonl(output_path, record)
        logger.info("[retraining] no metrics found, emitted SKIP record=%s", record)
        return record

    metrics = _load_jsonl(metrics_path)[-20:]
    if not metrics:
        logger.warning("[retraining] metrics file empty, falling back to no-metrics behavior")
        return run_training_review(Path("__missing__"), output_path)

    drift_values = {
        key: sum(float(row.get(key, 0.0)) for row in metrics) / len(metrics) for key in THRESHOLDS
    }
    should_retrain = any(drift_values[k] > THRESHOLDS[k] for k in THRESHOLDS)
    logger.info("[retraining] computed drift_values=%s should_retrain=%s", drift_values, should_retrain)
    logger.info("[retraining] sample recent metric row=%s", metrics[-1])

    record = TrainingReviewRecord(
        as_of=datetime.now(tz=ET),
        model_name="champion-v0",
        thresholds=THRESHOLDS,
        action="RETRAIN" if should_retrain else "SKIP",
        reason="Threshold breach detected" if should_retrain else "No material drift, calibration stable",
        **drift_values,
    )
    try:
        append_jsonl(output_path, record)
    except Exception as exc:
        logger.exception("[retraining] failed writing review output path=%s error=%s", output_path, exc)
        raise
    logger.info("[retraining] emitted review record=%s", record)
    return record
