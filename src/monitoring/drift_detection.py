from __future__ import annotations

import math
from collections import Counter
from typing import Any


def _safe_float(x: Any) -> float | None:
    try:
        if x is None:
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def _state_from_score(score: float, warn: float, fail: float) -> str:
    if score >= fail:
        return "RED"
    if score >= warn:
        return "YELLOW"
    return "GREEN"


def _quantile_bins(values: list[float], n_bins: int = 10) -> list[float]:
    if not values:
        return []
    s = sorted(values)
    cuts: list[float] = []
    for i in range(1, n_bins):
        idx = min(len(s) - 1, int((i / n_bins) * len(s)))
        cuts.append(s[idx])
    return cuts


def _hist(values: list[float], cuts: list[float]) -> list[float]:
    if not values:
        return [0.0] * (len(cuts) + 1)
    counts = [0] * (len(cuts) + 1)
    for v in values:
        i = 0
        while i < len(cuts) and v > cuts[i]:
            i += 1
        counts[i] += 1
    total = len(values)
    return [c / total for c in counts]


def psi(reference: list[Any], current: list[Any], n_bins: int = 10) -> float:
    ref = [x for x in (_safe_float(v) for v in reference) if x is not None]
    cur = [x for x in (_safe_float(v) for v in current) if x is not None]
    if len(ref) < 5 or len(cur) < 5:
        return 0.0
    cuts = _quantile_bins(ref, n_bins=n_bins)
    pr = _hist(ref, cuts)
    pc = _hist(cur, cuts)
    eps = 1e-8
    return sum((a - b) * math.log((a + eps) / (b + eps)) for a, b in zip(pr, pc))


def js_divergence(ref_counts: dict[str, int], cur_counts: dict[str, int]) -> float:
    keys = set(ref_counts) | set(cur_counts)
    if not keys:
        return 0.0
    total_r = sum(ref_counts.values()) or 1
    total_c = sum(cur_counts.values()) or 1
    p = {k: ref_counts.get(k, 0) / total_r for k in keys}
    q = {k: cur_counts.get(k, 0) / total_c for k in keys}
    m = {k: 0.5 * (p[k] + q[k]) for k in keys}

    def _kl(a: dict[str, float], b: dict[str, float]) -> float:
        eps = 1e-10
        return sum(a[k] * math.log((a[k] + eps) / (b[k] + eps)) for k in keys if a[k] > 0)

    return 0.5 * _kl(p, m) + 0.5 * _kl(q, m)


def evaluate_feature_data_drift(
    reference_rows: list[dict],
    current_rows: list[dict],
    important_features: list[str],
    psi_warn: float,
    psi_fail: float,
) -> dict:
    per_feature: dict[str, dict] = {}
    warn_count = 0
    fail_count = 0
    for feat in important_features:
        ref_vals = [r.get(feat) for r in reference_rows]
        cur_vals = [r.get(feat) for r in current_rows]
        score = psi(ref_vals, cur_vals)
        state = _state_from_score(score, psi_warn, psi_fail)
        if state == "RED":
            fail_count += 1
        elif state == "YELLOW":
            warn_count += 1
        per_feature[feat] = {"psi": score, "state": state}

    overall = "RED" if fail_count > 0 else "YELLOW" if warn_count > 0 else "GREEN"
    return {
        "state": overall,
        "features": per_feature,
        "warn_count": warn_count,
        "fail_count": fail_count,
    }


def evaluate_target_value_drift(reference_rows: list[dict], current_rows: list[dict], target_cols: list[str], js_warn: float, js_fail: float) -> dict:
    per_target: dict[str, dict] = {}
    red = 0
    yellow = 0
    for col in target_cols:
        ref = [x for x in (_safe_float(r.get(col)) for r in reference_rows) if x is not None]
        cur = [x for x in (_safe_float(r.get(col)) for r in current_rows) if x is not None]
        cuts = _quantile_bins(ref, 8) if ref else []
        ref_bins = Counter(str(sum(1 for c in cuts if v > c)) for v in ref)
        cur_bins = Counter(str(sum(1 for c in cuts if v > c)) for v in cur)
        score = js_divergence(dict(ref_bins), dict(cur_bins))
        state = _state_from_score(score, js_warn, js_fail)
        if state == "RED":
            red += 1
        elif state == "YELLOW":
            yellow += 1
        per_target[col] = {"js_divergence": score, "state": state}

    overall = "RED" if red else "YELLOW" if yellow else "GREEN"
    return {"state": overall, "targets": per_target}


def evaluate_target_temporal_drift(reference_rows: list[dict], current_rows: list[dict], temporal_cols: list[str], js_warn: float, js_fail: float) -> dict:
    per_target: dict[str, dict] = {}
    red = 0
    yellow = 0
    for col in temporal_cols:
        ref = Counter(str(r.get(col)) for r in reference_rows if r.get(col) is not None)
        cur = Counter(str(r.get(col)) for r in current_rows if r.get(col) is not None)
        score = js_divergence(dict(ref), dict(cur))
        state = _state_from_score(score, js_warn, js_fail)
        if state == "RED":
            red += 1
        elif state == "YELLOW":
            yellow += 1
        per_target[col] = {"js_divergence": score, "state": state}

    overall = "RED" if red else "YELLOW" if yellow else "GREEN"
    return {"state": overall, "targets": per_target}


def evaluate_coverage_calibration_drift(reference_metrics: dict, current_metrics: dict, warn: float, fail: float) -> dict:
    keys = ["coverage_error", "calibration_error"]
    deltas = {k: abs(float(current_metrics.get(k, 0.0)) - float(reference_metrics.get(k, 0.0))) for k in keys}
    max_delta = max(deltas.values()) if deltas else 0.0
    state = _state_from_score(max_delta, warn, fail)
    return {"state": state, "deltas": deltas, "max_delta": max_delta}


def evaluate_schema_and_coverage(reference_schema: dict, current_schema: dict, min_coverage: float) -> dict:
    ref_cols = set(reference_schema.get("columns", []))
    cur_cols = set(current_schema.get("columns", []))
    removed = sorted(ref_cols - cur_cols)
    added = sorted(cur_cols - ref_cols)

    coverage = current_schema.get("coverage_by_column", {})
    low_coverage = sorted([k for k, v in coverage.items() if float(v) < min_coverage])

    state = "GREEN"
    if removed or low_coverage:
        state = "RED"
    elif added:
        state = "YELLOW"

    return {
        "state": state,
        "removed_columns": removed,
        "added_columns": added,
        "low_coverage_columns": low_coverage,
    }


def evaluate_m3_data_quality(reference_rows: list[dict], current_rows: list[dict], required_cols: list[str], min_coverage: float) -> dict:
    def _coverage(rows: list[dict], col: str) -> float:
        if not rows:
            return 0.0
        non_null = sum(1 for r in rows if r.get(col) is not None)
        return non_null / max(len(rows), 1)

    columns: dict[str, dict] = {}
    worst = "GREEN"
    for col in required_cols:
        ref_cov = _coverage(reference_rows, col)
        cur_cov = _coverage(current_rows, col)
        delta = cur_cov - ref_cov
        state = "GREEN" if cur_cov >= min_coverage else "RED"
        if state == "RED":
            worst = "RED"
        columns[col] = {
            "reference_coverage": ref_cov,
            "current_coverage": cur_cov,
            "coverage_delta": delta,
            "state": state,
        }

    return {"state": worst, "columns": columns}


def evaluate_m3_value_and_timing_drift(reference_rows: list[dict], current_rows: list[dict], thresholds: dict) -> dict:
    value_drift = evaluate_target_value_drift(
        reference_rows,
        current_rows,
        target_cols=["realized_floor_m3"],
        js_warn=float(thresholds["realized_floor_m3_js_warn"]),
        js_fail=float(thresholds["realized_floor_m3_js_fail"]),
    )
    timing_drift = evaluate_target_temporal_drift(
        reference_rows,
        current_rows,
        temporal_cols=["floor_week_m3"],
        js_warn=float(thresholds["floor_week_m3_js_warn"]),
        js_fail=float(thresholds["floor_week_m3_js_fail"]),
    )

    target_states = {
        "realized_floor_m3": value_drift["targets"].get("realized_floor_m3", {"state": "GREEN"}),
        "floor_week_m3": timing_drift["targets"].get("floor_week_m3", {"state": "GREEN"}),
    }
    overall = "RED" if any(v.get("state") == "RED" for v in target_states.values()) else "YELLOW" if any(v.get("state") == "YELLOW" for v in target_states.values()) else "GREEN"
    return {
        "state": overall,
        "value_drift": value_drift,
        "timing_drift": timing_drift,
        "target_traffic_lights": target_states,
    }
