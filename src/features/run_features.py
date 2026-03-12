from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path

from features.feature_builder import build_features
from features.feature_registry import build_missingness_report, get_feature_registry
from features.labels import build_labels
from features.model_competition import build_model_competition_plan


def _to_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


def _load_rows(path: Path) -> list[dict]:
    if path.suffix == ".json" or path.suffix == ".jsonl":
        rows = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rows.append(json.loads(line))
        return rows

    if path.suffix == ".csv":
        with path.open("r", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    raise ValueError(f"Unsupported input format: {path}")


def _coerce_numeric(rows: list[dict]) -> list[dict]:
    num_cols = {
        "open",
        "high",
        "low",
        "close",
        "volume",
        "benchmark_close",
        "ai_conviction",
        "ai_floor_d1",
        "ai_ceiling_d1",
        "ai_floor_w1",
        "ai_ceiling_w1",
        "ai_floor_q1",
        "ai_ceiling_q1",
        "ai_floor_m3",
        "ai_conviction_long",
        "ai_recency_long",
        "ai_consensus_score",
    }
    for row in rows:
        for col in num_cols:
            if row.get(col) in (None, ""):
                row[col] = None
            elif col in row:
                row[col] = float(row[col])
    return rows


def build_walk_forward_splits(rows: list[dict], train_days: int = 40, valid_days: int = 10, test_days: int = 10, step_days: int = 10) -> list[dict]:
    ordered_days = sorted({_to_datetime(r["timestamp"]).date() for r in rows})
    folds = []
    start = 0
    fold_id = 1
    while start + train_days + valid_days + test_days <= len(ordered_days):
        train_slice = ordered_days[start : start + train_days]
        valid_slice = ordered_days[start + train_days : start + train_days + valid_days]
        test_slice = ordered_days[start + train_days + valid_days : start + train_days + valid_days + test_days]
        folds.append(
            {
                "fold": fold_id,
                "train_start": str(train_slice[0]),
                "train_end": str(train_slice[-1]),
                "valid_start": str(valid_slice[0]),
                "valid_end": str(valid_slice[-1]),
                "test_start": str(test_slice[0]),
                "test_end": str(test_slice[-1]),
            }
        )
        start += step_days
        fold_id += 1
    return folds


def assign_split(rows: list[dict], train_ratio: float = 0.7, valid_ratio: float = 0.15) -> list[dict]:
    days = sorted({_to_datetime(r["timestamp"]).date() for r in rows})
    n = len(days)
    train_end = int(n * train_ratio)
    valid_end = int(n * (train_ratio + valid_ratio))
    train_days = set(days[:train_end])
    valid_days = set(days[train_end:valid_end])

    for row in rows:
        day = _to_datetime(row["timestamp"]).date()
        if day in train_days:
            row["split"] = "train"
        elif day in valid_days:
            row["split"] = "validation"
        else:
            row["split"] = "test"
    return rows


def _horizon_coverage(rows: list[dict], horizon: str, columns: list[str]) -> dict:
    total = max(1, len(rows))
    return {
        "horizon": horizon,
        "rows": len(rows),
        "coverage": {c: sum(1 for r in rows if r.get(c) is not None) / total for c in columns},
    }


def build_modelable_dataset(rows: list[dict]) -> dict:
    rows = _coerce_numeric(rows)
    feat_rows = build_features(rows)
    labeled_rows = build_labels(feat_rows)
    labeled_rows = assign_split(labeled_rows)
    wf = build_walk_forward_splits(labeled_rows)

    registry = [spec.__dict__ for spec in get_feature_registry()]
    competition_plan = build_model_competition_plan()
    final_columns = sorted({k for row in labeled_rows for k in row.keys()})
    missingness = build_missingness_report(labeled_rows, final_columns)

    target_definitions = {
        "floor_targets": "floor_h = min(low) in forward horizon h where h in {d1,w1,q1,m3}.",
        "ceiling_targets": "ceiling_h = max(high) in forward horizon h where h in {d1,w1,q1}.",
        "m3_target": {
            "floor_m3": "realized minimum low over next 13 relative market weeks (up to 65 future sessions).",
            "floor_week_m3": "relative week class 1..13 containing the realized floor_m3.",
            "week_assignment": "build contiguous future week chunks of up to 5 trading sessions; holidays/incomplete weeks keep available sessions.",
            "tie_break_rule": "if two weeks share identical floor low, choose earliest relative week index for stability.",
            "extra_outputs": [
                "realized_range_m3",
                "forward_return_m3",
                "floor_breach_flag_m3",
                "floor_week_m3_start_date",
                "floor_week_m3_end_date",
            ],
        },
        "temporal_targets": {
            "d1": "Event timestamp is labeled at bar resolution, then mapped to OPEN/OPEN_PLUS_2H/OPEN_PLUS_4H/OPEN_PLUS_6H/CLOSE.",
            "w1": "Relative business day index (1..5) where floor/ceiling occurs within next 5 trading days.",
            "q1": "Relative business day index (1..10) where floor/ceiling occurs within next 10 trading days.",
        },
        "calculation_windows": {
            "returns": [1, 2, 5, 10],
            "volatility": [5, 20, 60],
            "momentum": [10, 14, 20, 40, 65],
            "atr": 14,
            "relative_volume": 20,
            "rolling_extremes": [20, 60, 63, 126, 252],
            "beta_relative_strength": 20,
        },
    }

    m3_coverage = _horizon_coverage(
        labeled_rows,
        horizon="m3",
        columns=[
            "floor_m3",
            "realized_floor_m3",
            "floor_week_m3",
            "realized_range_m3",
            "forward_return_m3",
            "floor_breach_flag_m3",
            "floor_week_m3_start_date",
            "floor_week_m3_end_date",
        ],
    )

    return {
        "rows": labeled_rows,
        "feature_registry": registry,
        "walk_forward_folds": wf,
        "missingness_report": missingness,
        "target_documentation": target_definitions,
        "final_model_columns": final_columns,
        "model_competition": competition_plan,
        "horizon_coverage": {"m3": m3_coverage},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build floor/ceiling modelable dataset")
    parser.add_argument("--input", required=True, help="Input path (.csv|.jsonl)")
    parser.add_argument("--output", required=True, help="Output JSON path")
    args = parser.parse_args()

    rows = _load_rows(Path(args.input))
    artifact = build_modelable_dataset(rows)
    Path(args.output).write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
