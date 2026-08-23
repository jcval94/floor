"""Trained timing model for d1/w1/q1 horizon extrema.

The model is intentionally small and auditable: it learns class distributions
conditioned on volatility tercile and trend sign. The important contract is
that timing comes from observed timing labels, not a hard-coded model-family
lookup table.

d1 timing is unavailable when training data contains only one OHLC bar per
session; in that case the artifact explicitly records an unavailable status.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

D1_CLASSES = ("OPEN", "OPEN_PLUS_2H", "OPEN_PLUS_4H", "OPEN_PLUS_6H", "CLOSE")
ALLOWED_CLASSES: dict[str, tuple[str, ...]] = {
    "d1": D1_CLASSES,
    "w1": tuple(str(i) for i in range(1, 6)),
    "q1": tuple(str(i) for i in range(1, 11)),
}
TARGET_COLUMNS = {
    "d1": ("floor_time_bucket_d1", "ceiling_time_bucket_d1"),
    "w1": ("floor_day_w1", "ceiling_day_w1"),
    "q1": ("floor_day_q1", "ceiling_day_q1"),
}


def _to_float(value: object, default: float = 0.0) -> float:
    if not isinstance(value, (int, float, str, bytes, bytearray)):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * q))))
    return float(ordered[idx])


def _vol_ratio(row: dict) -> float:
    close = max(_to_float(row.get("close"), 0.0), 1e-9)
    return abs(_to_float(row.get("atr_14"), 0.0)) / close


def _state_key(row: dict, cuts: list[float]) -> str:
    vol = _vol_ratio(row)
    bucket = len(cuts) + 1
    for idx, cut in enumerate(cuts, start=1):
        if vol <= cut:
            bucket = idx
            break
    trend = "up" if _to_float(row.get("trend_context_m3"), 0.0) >= 0 else "down"
    return f"v{bucket}:{trend}"


def _normalize_label(value: object, horizon: str) -> str | None:
    if value in (None, ""):
        return None
    if horizon == "d1":
        label = str(value)
    else:
        if not isinstance(value, (int, float, str, bytes, bytearray)):
            return None
        try:
            label = str(int(value))
        except (TypeError, ValueError):
            return None
    return label if label in ALLOWED_CLASSES[horizon] else None


def _distribution(counts: Counter[str], classes: tuple[str, ...]) -> dict[str, float]:
    total = sum(counts.values()) + len(classes)
    return {label: (counts.get(label, 0) + 1) / total for label in classes}


def _fit_side(
    rows: list[dict],
    target_col: str,
    horizon: str,
    cuts: list[float],
) -> dict[str, Any]:
    classes = ALLOWED_CLASSES[horizon]
    global_counts: Counter[str] = Counter()
    state_counts: dict[str, Counter[str]] = defaultdict(Counter)

    for row in rows:
        label = _normalize_label(row.get(target_col), horizon)
        if label is None:
            continue
        global_counts[label] += 1
        state_counts[_state_key(row, cuts)][label] += 1

    if not global_counts:
        return {"rows": 0, "global": {}, "table": {}}

    return {
        "rows": sum(global_counts.values()),
        "global": _distribution(global_counts, classes),
        "table": {
            state: _distribution(counts, classes)
            for state, counts in sorted(state_counts.items())
        },
    }


def fit_horizon_timing(rows: list[dict], horizon: str) -> dict[str, Any]:
    if horizon not in ALLOWED_CLASSES:
        raise ValueError(f"Unsupported timing horizon: {horizon}")

    eligible_key = f"split_eligible_{horizon}"
    eligible_rows = [
        row
        for row in rows
        if row.get(eligible_key, True) is not False
    ]

    floor_col, ceiling_col = TARGET_COLUMNS[horizon]
    rows_with_target = [
        row
        for row in eligible_rows
        if _normalize_label(row.get(floor_col), horizon) is not None
        or _normalize_label(row.get(ceiling_col), horizon) is not None
    ]

    if horizon == "d1" and not rows_with_target:
        return {
            "schema_version": 2,
            "horizon": horizon,
            "status": "unavailable_daily_resolution",
            "classes": list(ALLOWED_CLASSES[horizon]),
            "train_rows": 0,
            "vol_cuts": [],
            "floor": {"rows": 0, "global": {}, "table": {}},
            "ceiling": {"rows": 0, "global": {}, "table": {}},
        }

    if not rows_with_target:
        return {
            "schema_version": 2,
            "horizon": horizon,
            "status": "unavailable_insufficient_labels",
            "classes": list(ALLOWED_CLASSES[horizon]),
            "train_rows": 0,
            "vol_cuts": [],
            "floor": {"rows": 0, "global": {}, "table": {}},
            "ceiling": {"rows": 0, "global": {}, "table": {}},
        }

    vol_values = [_vol_ratio(row) for row in rows_with_target]
    cuts = [_quantile(vol_values, 1 / 3), _quantile(vol_values, 2 / 3)]

    floor = _fit_side(rows_with_target, floor_col, horizon, cuts)
    ceiling = _fit_side(rows_with_target, ceiling_col, horizon, cuts)
    trained_rows = min(int(floor["rows"]), int(ceiling["rows"]))

    status = "trained" if trained_rows > 0 else "unavailable_insufficient_labels"
    return {
        "schema_version": 2,
        "horizon": horizon,
        "status": status,
        "classes": list(ALLOWED_CLASSES[horizon]),
        "train_rows": trained_rows,
        "vol_cuts": cuts,
        "state_features": ["atr_14/close", "sign(trend_context_m3)"],
        "floor": floor,
        "ceiling": ceiling,
    }


def _validate_params(params: dict[str, Any], horizon: str) -> None:
    if int(params.get("schema_version") or 0) != 2:
        raise ValueError(f"Timing artifact {horizon} missing schema_version=2")
    if str(params.get("horizon") or "") != horizon:
        raise ValueError(f"Timing artifact horizon mismatch: expected={horizon}")
    expected = list(ALLOWED_CLASSES[horizon])
    if list(params.get("classes") or []) != expected:
        raise ValueError(
            f"Timing artifact classes mismatch for {horizon}: "
            f"expected={expected} actual={params.get('classes')}"
        )


def predict_horizon_timing(
    row: dict,
    params: dict[str, Any],
    horizon: str,
    side: str,
) -> tuple[str | int | None, float]:
    if side not in {"floor", "ceiling"}:
        raise ValueError("side must be floor or ceiling")
    _validate_params(params, horizon)

    status = str(params.get("status") or "")
    if status != "trained":
        return None, 0.0

    side_params = params.get(side)
    if not isinstance(side_params, dict):
        raise ValueError(f"Timing artifact {horizon} missing {side} parameters")

    cuts_raw = params.get("vol_cuts")
    cuts = [_to_float(v) for v in cuts_raw] if isinstance(cuts_raw, list) else []
    key = _state_key(row, cuts)

    table = side_params.get("table")
    global_probs = side_params.get("global")
    if not isinstance(global_probs, dict) or not global_probs:
        raise ValueError(f"Timing artifact {horizon} {side} missing global distribution")

    probs: dict[str, float]
    if isinstance(table, dict) and isinstance(table.get(key), dict):
        probs = {str(k): _to_float(v) for k, v in table[key].items()}
    else:
        probs = {str(k): _to_float(v) for k, v in global_probs.items()}

    allowed = ALLOWED_CLASSES[horizon]
    if any(label not in allowed for label in probs):
        raise ValueError(f"Timing artifact {horizon} contains out-of-domain class")

    label = max(allowed, key=lambda cls: probs.get(cls, 0.0))
    probability = max(0.0, min(1.0, probs.get(label, 0.0)))

    if horizon == "d1":
        return label, probability
    numeric = int(label)
    upper = 5 if horizon == "w1" else 10
    if not 1 <= numeric <= upper:
        raise ValueError(f"Timing prediction out of domain horizon={horizon}: {numeric}")
    return numeric, probability