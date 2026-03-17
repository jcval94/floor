from __future__ import annotations

import argparse
import csv
import json
import logging
from dataclasses import dataclass, asdict
from pathlib import Path
from statistics import median

logger = logging.getLogger(__name__)

HORIZON_TARGETS = {
    "d1": ("floor_d1", "ceiling_d1"),
    "w1": ("floor_w1", "ceiling_w1"),
    "q1": ("floor_q1", "ceiling_q1"),
}


@dataclass
class HorizonBaselineArtifact:
    horizon: str
    model_name: str
    version: str
    floor_delta: float
    ceiling_delta: float
    train_rows: int
    test_rows: int
    metrics: dict[str, float]


def _load_rows(dataset_path: Path) -> list[dict]:
    payload = json.loads(dataset_path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "rows" in payload:
        rows = payload["rows"]
    elif isinstance(payload, list):
        rows = payload
    else:
        raise ValueError(f"Unsupported dataset payload: {dataset_path}")
    if not isinstance(rows, list):
        raise ValueError("Dataset rows must be a list")
    return rows


def _split(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    train = [r for r in rows if r.get("split") == "train"]
    test = [r for r in rows if r.get("split") in {"validation", "test"}]
    if not train:
        pivot = int(len(rows) * 0.7)
        train = rows[:pivot]
        test = rows[pivot:]
    return train, test


def _to_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def train_horizon_baseline(rows: list[dict], horizon: str, version: str) -> HorizonBaselineArtifact:
    if horizon not in HORIZON_TARGETS:
        raise ValueError(f"Unsupported horizon: {horizon}")

    floor_col, ceiling_col = HORIZON_TARGETS[horizon]
    train_rows, test_rows = _split(rows)

    floor_deltas: list[float] = []
    ceiling_deltas: list[float] = []

    for row in train_rows:
        close = _to_float(row.get("close"))
        floor = _to_float(row.get(floor_col))
        ceiling = _to_float(row.get(ceiling_col))
        if close is None or close <= 0:
            continue
        if floor is not None:
            floor_deltas.append(max(0.0, (close - floor) / close))
        if ceiling is not None:
            ceiling_deltas.append(max(0.0, (ceiling - close) / close))

    floor_delta = float(median(floor_deltas)) if floor_deltas else 0.01
    ceiling_delta = float(median(ceiling_deltas)) if ceiling_deltas else 0.01

    floor_errors: list[float] = []
    ceiling_errors: list[float] = []
    spread_errors: list[float] = []

    for row in test_rows:
        close = _to_float(row.get("close"))
        floor = _to_float(row.get(floor_col))
        ceiling = _to_float(row.get(ceiling_col))
        if close is None or close <= 0:
            continue
        pred_floor = close * (1.0 - floor_delta)
        pred_ceiling = close * (1.0 + ceiling_delta)
        if floor is not None:
            floor_errors.append(abs(pred_floor - floor))
        if ceiling is not None:
            ceiling_errors.append(abs(pred_ceiling - ceiling))
        if floor is not None and ceiling is not None:
            spread_errors.append(abs((pred_ceiling - pred_floor) - (ceiling - floor)))

    denom_floor = max(1, len(floor_errors))
    denom_ceiling = max(1, len(ceiling_errors))
    denom_spread = max(1, len(spread_errors))

    metrics = {
        "mae_floor": sum(floor_errors) / denom_floor,
        "mae_ceiling": sum(ceiling_errors) / denom_ceiling,
        "mae_spread": sum(spread_errors) / denom_spread,
        "test_floor_coverage": len(floor_errors) / max(1, len(test_rows)),
        "test_ceiling_coverage": len(ceiling_errors) / max(1, len(test_rows)),
    }

    return HorizonBaselineArtifact(
        horizon=horizon,
        model_name=f"{horizon}_baseline_median_delta",
        version=version,
        floor_delta=floor_delta,
        ceiling_delta=ceiling_delta,
        train_rows=len(train_rows),
        test_rows=len(test_rows),
        metrics=metrics,
    )


def run(dataset_path: Path, output_dir: Path, version: str) -> Path:
    rows = _load_rows(dataset_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    artifacts: list[HorizonBaselineArtifact] = []
    for horizon in ("d1", "w1", "q1"):
        artifact = train_horizon_baseline(rows, horizon=horizon, version=version)
        artifacts.append(artifact)
        out_path = output_dir / f"{horizon}_champion.json"
        out_path.write_text(json.dumps(asdict(artifact), ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(
            "[horizon-training] horizon=%s train_rows=%s test_rows=%s mae_floor=%.6f mae_ceiling=%.6f",
            horizon,
            artifact.train_rows,
            artifact.test_rows,
            artifact.metrics["mae_floor"],
            artifact.metrics["mae_ceiling"],
        )

    csv_path = output_dir / f"horizon_training_results_{version}.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "horizon",
                "model_name",
                "version",
                "train_rows",
                "test_rows",
                "floor_delta",
                "ceiling_delta",
                "mae_floor",
                "mae_ceiling",
                "mae_spread",
                "test_floor_coverage",
                "test_ceiling_coverage",
            ],
        )
        writer.writeheader()
        for artifact in artifacts:
            writer.writerow(
                {
                    "horizon": artifact.horizon,
                    "model_name": artifact.model_name,
                    "version": artifact.version,
                    "train_rows": artifact.train_rows,
                    "test_rows": artifact.test_rows,
                    "floor_delta": round(artifact.floor_delta, 8),
                    "ceiling_delta": round(artifact.ceiling_delta, 8),
                    "mae_floor": round(float(artifact.metrics["mae_floor"]), 8),
                    "mae_ceiling": round(float(artifact.metrics["mae_ceiling"]), 8),
                    "mae_spread": round(float(artifact.metrics["mae_spread"]), 8),
                    "test_floor_coverage": round(float(artifact.metrics["test_floor_coverage"]), 8),
                    "test_ceiling_coverage": round(float(artifact.metrics["test_ceiling_coverage"]), 8),
                }
            )

    logger.info("[horizon-training] wrote csv summary=%s", csv_path)
    return csv_path


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    parser = argparse.ArgumentParser(description="Train d1/w1/q1 baseline horizon champions and export CSV summary")
    parser.add_argument("--dataset", required=True, help="Path to modelable_dataset.json")
    parser.add_argument("--output-dir", default="data/training/models", help="Directory for champion JSON and CSV report")
    parser.add_argument("--version", required=True, help="Version tag")
    args = parser.parse_args()

    run(Path(args.dataset), Path(args.output_dir), args.version)


if __name__ == "__main__":
    main()
