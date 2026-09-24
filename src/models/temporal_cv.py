from __future__ import annotations

from datetime import date, datetime


def _as_date(value: object) -> date | None:
    if value in (None, ""):
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return None


def purged_expanding_folds(
    rows: list[dict],
    *,
    target_end_field: str,
    folds: int = 3,
    min_train_dates: int = 20,
) -> list[tuple[list[dict], list[dict]]]:
    """Build expanding time folds while purging labels that cross validation.

    Rows are split on distinct observation dates, never individual rows, so all
    symbols observed on the same date remain in the same fold. A training row is
    admitted only when its target end date is strictly before the validation
    start date. This prevents forward labels from leaking across fold boundaries.
    """

    dated: list[tuple[date, date, dict]] = []
    for row in rows:
        observed = _as_date(row.get("timestamp"))
        target_end = _as_date(row.get(target_end_field))
        if observed is None or target_end is None:
            continue
        dated.append((observed, target_end, row))

    distinct_dates = sorted({observed for observed, _, _ in dated})
    if len(distinct_dates) < max(min_train_dates + folds, folds * 4):
        return []

    # Reserve roughly one (folds + 1)-th of the history for each validation
    # block and expand the training history on every fold.
    block = max(1, len(distinct_dates) // (folds + 1))
    result: list[tuple[list[dict], list[dict]]] = []
    for fold_idx in range(1, folds + 1):
        valid_start_idx = fold_idx * block
        if valid_start_idx >= len(distinct_dates):
            break
        valid_end_idx = (
            min(len(distinct_dates), valid_start_idx + block)
            if fold_idx < folds
            else len(distinct_dates)
        )
        valid_dates = set(distinct_dates[valid_start_idx:valid_end_idx])
        if not valid_dates:
            continue
        valid_start = min(valid_dates)

        train = [
            row
            for observed, target_end, row in dated
            if observed < valid_start and target_end < valid_start
        ]
        valid = [row for observed, _, row in dated if observed in valid_dates]
        train_dates = {_as_date(row.get("timestamp")) for row in train}
        if len(train_dates) >= min_train_dates and train and valid:
            result.append((train, valid))

    return result


def chronological_calibration_split(
    rows: list[dict],
    *,
    calibration_fraction: float = 0.5,
) -> tuple[list[dict], list[dict]]:
    """Split validation chronologically into calibration then evaluation.

    Calibration never sees the later evaluation rows. If timestamps are absent,
    input order is preserved as the deterministic chronological fallback.
    """

    if len(rows) < 4:
        return list(rows), list(rows)

    def key(row: dict) -> tuple[str, str]:
        return (str(row.get("timestamp") or ""), str(row.get("symbol") or ""))

    ordered = sorted(rows, key=key)
    cut = int(len(ordered) * calibration_fraction)
    cut = max(1, min(len(ordered) - 1, cut))
    return ordered[:cut], ordered[cut:]



def purged_chronological_calibration_split(
    rows: list[dict],
    *,
    target_end_field: str,
    calibration_fraction: float = 0.5,
) -> tuple[list[dict], list[dict]]:
    """Split validation by whole observation dates and purge crossing labels.

    The later block is a true out-of-time evaluation set.  Calibration rows are
    retained only when their forward target is fully known before evaluation
    begins.  When point-in-time metadata is absent, fall back to the legacy
    chronological split for synthetic/backward-compatible inputs.
    """

    dated: list[tuple[date, date, dict]] = []
    for row in rows:
        observed = _as_date(row.get("timestamp"))
        target_end = _as_date(row.get(target_end_field))
        if observed is None or target_end is None:
            continue
        dated.append((observed, target_end, row))

    distinct_dates = sorted({observed for observed, _, _ in dated})
    if len(dated) != len(rows) or len(distinct_dates) < 2:
        return chronological_calibration_split(
            rows,
            calibration_fraction=calibration_fraction,
        )

    cut = int(len(distinct_dates) * calibration_fraction)
    cut = max(1, min(len(distinct_dates) - 1, cut))
    calibration_dates = set(distinct_dates[:cut])
    evaluation_dates = set(distinct_dates[cut:])
    evaluation_start = min(evaluation_dates)

    calibration = [
        row
        for observed, target_end, row in dated
        if observed in calibration_dates and target_end < evaluation_start
    ]
    evaluation = [
        row
        for observed, _, row in dated
        if observed in evaluation_dates
    ]
    return calibration, evaluation
