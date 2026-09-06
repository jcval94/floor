from __future__ import annotations

import argparse
import csv
import json
import logging
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from features.feature_builder import build_features
from features.feature_registry import build_missingness_report, get_feature_registry
from features.labels import HORIZON_SESSIONS, build_labels
from features.model_competition import build_model_competition_plan

logger = logging.getLogger(__name__)


def _to_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


def _load_rows(path: Path) -> list[dict]:
    logger.info("[etl:features] loading input path=%s", path)
    if path.suffix in {".json", ".jsonl"}:
        rows = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        logger.info("[etl:features] loaded json/jsonl rows=%s", len(rows))
        return rows

    if path.suffix == ".csv":
        with path.open("r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        logger.info("[etl:features] loaded csv rows=%s", len(rows))
        return rows

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
    for idx, row in enumerate(rows):
        for col in num_cols:
            try:
                if row.get(col) in (None, ""):
                    row[col] = None
                elif col in row:
                    row[col] = float(row[col])
            except (TypeError, ValueError) as exc:
                logger.warning(
                    "[etl:features] numeric cast failed row=%s col=%s value=%s error=%s",
                    idx,
                    col,
                    row.get(col),
                    exc,
                )
                row[col] = None
    return rows


def build_walk_forward_splits(
    rows: list[dict],
    train_days: int = 40,
    valid_days: int = 10,
    test_days: int = 10,
    step_days: int = 10,
) -> list[dict]:
    """Describe chronological folds.

    The production trainers rely on per-horizon ``split_eligible_*`` flags for
    leakage protection. These fold descriptors are retained for audit/reporting.
    """
    ordered_days = sorted({_to_datetime(r["timestamp"]).date() for r in rows})
    folds = []
    start = 0
    fold_id = 1
    while start + train_days + valid_days + test_days <= len(ordered_days):
        train_slice = ordered_days[start : start + train_days]
        valid_slice = ordered_days[
            start + train_days : start + train_days + valid_days
        ]
        test_slice = ordered_days[
            start + train_days + valid_days :
            start + train_days + valid_days + test_days
        ]
        folds.append(
            {
                "fold": fold_id,
                "train_start": str(train_slice[0]),
                "train_end": str(train_slice[-1]),
                "valid_start": str(valid_slice[0]),
                "valid_end": str(valid_slice[-1]),
                "test_start": str(test_slice[0]),
                "test_end": str(test_slice[-1]),
                "eligibility_contract": "use split_eligible_<horizon> before fitting/evaluation",
            }
        )
        start += step_days
        fold_id += 1
    return folds


def _date_from_iso(value: object) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _annotate_split_eligibility(rows: list[dict], split_end: dict[str, date]) -> None:
    """Mark rows whose full forward label remains inside their own split.

    This is the purge/embargo contract used by training. A row can retain its
    chronological split label for reporting while being ineligible for one or
    more horizons.
    """
    for row in rows:
        split = str(row.get("split") or "")
        boundary = split_end.get(split)
        for horizon in HORIZON_SESSIONS:
            complete = bool(row.get(f"horizon_complete_{horizon}", False))
            target_end = _date_from_iso(row.get(f"target_end_date_{horizon}"))
            eligible = bool(
                boundary is not None
                and complete
                and target_end is not None
                and target_end <= boundary
            )
            row[f"split_eligible_{horizon}"] = eligible
            if eligible:
                row[f"split_ineligible_reason_{horizon}"] = None
            elif not complete:
                row[f"split_ineligible_reason_{horizon}"] = "incomplete_future_horizon"
            elif target_end is None:
                row[f"split_ineligible_reason_{horizon}"] = "missing_target_end"
            else:
                row[f"split_ineligible_reason_{horizon}"] = "target_crosses_split_boundary"


def assign_split(
    rows: list[dict],
    train_ratio: float = 0.7,
    valid_ratio: float = 0.15,
    *,
    validation_days: int | None = None,
    test_days: int | None = None,
) -> list[dict]:
    """Assign chronological splits and horizon-specific leakage-safe eligibility."""
    days = sorted({_to_datetime(r["timestamp"]).date() for r in rows})
    n = len(days)
    if n == 0:
        return rows

    if (validation_days is None) != (test_days is None):
        raise ValueError("validation_days and test_days must be supplied together")
    if validation_days is not None and test_days is not None:
        if validation_days <= 0 or test_days <= 0:
            raise ValueError("fixed-tail validation/test days must be positive")
        if validation_days + test_days >= n:
            raise ValueError("fixed-tail validation/test windows leave no training days")
        train_end_idx = n - validation_days - test_days
        valid_end_idx = n - test_days
    else:
        train_end_idx = max(1, min(n, int(n * train_ratio)))
        valid_end_idx = max(
            train_end_idx, min(n, int(n * (train_ratio + valid_ratio)))
        )

    train_days = set(days[:train_end_idx])
    valid_days = set(days[train_end_idx:valid_end_idx])
    held_out_days = set(days[valid_end_idx:])

    for row in rows:
        day = _to_datetime(row["timestamp"]).date()
        if day in train_days:
            row["split"] = "train"
        elif day in valid_days:
            row["split"] = "validation"
        else:
            row["split"] = "test"

    split_end: dict[str, date] = {}
    if train_days:
        split_end["train"] = max(train_days)
    if valid_days:
        split_end["validation"] = max(valid_days)
    if held_out_days:
        split_end["test"] = max(held_out_days)

    _annotate_split_eligibility(rows, split_end)
    return rows


def _horizon_coverage(rows: list[dict], horizon: str, columns: list[str]) -> dict:
    total = max(1, len(rows))
    return {
        "horizon": horizon,
        "rows": len(rows),
        "coverage": {
            c: sum(1 for r in rows if r.get(c) is not None) / total
            for c in columns
        },
        "split_eligible_rows": sum(
            1 for r in rows if r.get(f"split_eligible_{horizon}") is True
        ),
    }


def build_modelable_dataset(
    rows: list[dict],
    *,
    validation_days: int | None = None,
    test_days: int | None = None,
) -> dict:
    logger.info("[etl:features] building modelable dataset input_rows=%s", len(rows))
    rows = _coerce_numeric(rows)

    try:
        feat_rows = build_features(rows)
        logger.info("[etl:features] feature rows=%s", len(feat_rows))
    except Exception as exc:
        logger.exception("[etl:features] build_features failed: %s", exc)
        raise

    try:
        labeled_rows = build_labels(feat_rows)
        labeled_rows = assign_split(
            labeled_rows,
            validation_days=validation_days,
            test_days=test_days,
        )
        wf = build_walk_forward_splits(labeled_rows)
    except Exception as exc:
        logger.exception("[etl:features] labeling/splitting failed: %s", exc)
        raise

    registry = [spec.__dict__ for spec in get_feature_registry()]
    competition_plan = build_model_competition_plan()
    final_columns = sorted({k for row in labeled_rows for k in row.keys()})
    missingness = build_missingness_report(labeled_rows, final_columns)

    target_definitions = {
        "floor_targets": (
            "floor_h = min(low) over the complete forward horizon h. "
            "Rows without the full horizon are right-censored and receive null targets."
        ),
        "ceiling_targets": (
            "ceiling_h = max(high) over the complete forward horizon h. "
            "Rows without the full horizon are right-censored and receive null targets."
        ),
        "split_integrity": {
            "rule": (
                "A row is fit/evaluation eligible for horizon h only when "
                "target_end_date_h remains inside the row's chronological split."
            ),
            "fields": [
                "split_eligible_d1",
                "split_eligible_w1",
                "split_eligible_q1",
                "split_eligible_m3",
            ],
        },
        "m3_target": {
            "floor_m3": "realized minimum low over exactly 65 future trading sessions.",
            "floor_delta_m3": "(close_t - floor_m3) / close_t, clipped to [0, 0.95].",
            "floor_week_m3": "relative week class 1..13 containing floor_m3.",
            "week_assignment": "13 contiguous chunks of exactly 5 future trading sessions.",
            "tie_break_rule": "identical minima choose the earliest relative week.",
            "extra_outputs": [
                "realized_range_m3",
                "forward_return_m3",
                "floor_breach_flag_m3",
                "floor_week_m3_start_date",
                "floor_week_m3_end_date",
            ],
        },
        "temporal_targets": {
            "d1": (
                "Intraday timing is emitted only when the source has multiple bars "
                "inside the next session; daily OHLC input leaves timing null."
            ),
            "w1": "Relative business-day index 1..5 of the realized extreme.",
            "q1": "Relative business-day index 1..10 of the realized extreme.",
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

    horizon_coverage = {
        horizon: _horizon_coverage(
            labeled_rows,
            horizon,
            (
                ["floor_m3", "floor_delta_m3", "floor_week_m3"]
                if horizon == "m3"
                else [f"floor_{horizon}", f"ceiling_{horizon}"]
            ),
        )
        for horizon in ("d1", "w1", "q1", "m3")
    }

    split_counts = Counter(str(r.get("split") or "") for r in labeled_rows)
    artifact = {
        "rows": labeled_rows,
        "feature_registry": registry,
        "walk_forward_folds": wf,
        "missingness_report": missingness,
        "target_documentation": target_definitions,
        "final_model_columns": final_columns,
        "model_competition": competition_plan,
        "horizon_coverage": horizon_coverage,
        "split_counts": dict(split_counts),
        "split_policy": (
            {
                "strategy": "fixed_tail_sessions",
                "validation_days": validation_days,
                "test_days": test_days,
            }
            if validation_days is not None and test_days is not None
            else {
                "strategy": "chronological_ratio",
                "train_ratio": 0.7,
                "validation_ratio": 0.15,
                "test_ratio": 0.15,
            }
        ),
    }
    logger.info(
        "[etl:features] artifact ready rows=%s folds=%s columns=%s split_counts=%s",
        len(labeled_rows),
        len(wf),
        len(final_columns),
        dict(split_counts),
    )
    return artifact


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    parser = argparse.ArgumentParser(
        description="Build leakage-safe floor/ceiling modelable dataset"
    )
    parser.add_argument("--input", required=True, help="Input path (.csv|.jsonl)")
    parser.add_argument("--output", required=True, help="Output JSON path")
    parser.add_argument(
        "--validation-days",
        type=int,
        default=None,
        help="Use this many trailing sessions before the test window for validation",
    )
    parser.add_argument(
        "--test-days",
        type=int,
        default=None,
        help="Reserve this many trailing sessions as a sealed chronological test",
    )
    args = parser.parse_args()

    try:
        rows = _load_rows(Path(args.input))
        artifact = build_modelable_dataset(
            rows,
            validation_days=args.validation_days,
            test_days=args.test_days,
        )
        Path(args.output).write_text(
            json.dumps(artifact, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("[etl:features] wrote modelable dataset path=%s", args.output)
    except Exception as exc:
        logger.exception("[etl:features] CLI failed: %s", exc)
        raise


if __name__ == "__main__":
    main()
