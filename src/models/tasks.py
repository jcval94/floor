from __future__ import annotations

VALID_MODEL_TASKS = ("value", "timing")


def normalize_model_tasks(tasks: str | list[str] | tuple[str, ...] | None) -> list[str]:
    if tasks is None:
        return list(VALID_MODEL_TASKS)

    if isinstance(tasks, str):
        parts = [part.strip().lower() for part in tasks.split(",") if part.strip()]
    else:
        parts = [str(part).strip().lower() for part in tasks if str(part).strip()]

    if not parts:
        return list(VALID_MODEL_TASKS)

    normalized: list[str] = []
    invalid: list[str] = []
    for part in parts:
        if part not in VALID_MODEL_TASKS:
            invalid.append(part)
            continue
        if part not in normalized:
            normalized.append(part)

    if invalid:
        raise ValueError(f"Unsupported model tasks: {', '.join(invalid)}")

    return normalized
