from __future__ import annotations

from statistics import mean, pstdev


def _safe_float(value: object) -> float | None:
    try:
        if value in (None, ""):
            return None
        if not isinstance(value, (int, float, str)):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def summarize_modelable_rows(rows: list[dict]) -> dict:
    columns = sorted({key for row in rows for key in row.keys()})
    total = max(len(rows), 1)

    coverage_by_column = {
        column: sum(1 for row in rows if row.get(column) not in (None, "")) / total
        for column in columns
    }

    numeric_stats: dict[str, dict] = {}
    for column in columns:
        values = [_safe_float(row.get(column)) for row in rows]
        clean = [value for value in values if value is not None]
        if not clean:
            continue
        numeric_stats[column] = {
            "count": len(clean),
            "mean": mean(clean),
            "std": pstdev(clean) if len(clean) > 1 else 0.0,
            "min": min(clean),
            "max": max(clean),
        }

    categorical_counts: dict[str, dict[str, int]] = {}
    for column in ["split", "floor_week_m3"]:
        column_counts: dict[str, int] = {}
        for row in rows:
            raw = row.get(column)
            if raw in (None, ""):
                continue
            category = str(raw)
            column_counts[category] = column_counts.get(category, 0) + 1
        if column_counts:
            categorical_counts[column] = column_counts

    return {
        "rows": len(rows),
        "columns": columns,
        "coverage_by_column": coverage_by_column,
        "numeric_stats": numeric_stats,
        "categorical_counts": categorical_counts,
    }
