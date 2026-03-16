from __future__ import annotations

from collections.abc import Iterable

VALID_MODEL_TASKS = ("value", "timing")


def normalize_model_tasks(tasks: str | Iterable[str] | None) -> list[str]:
    """Normalize training tasks into an ordered, unique list.

    Contract:
    - ``None`` or empty input returns all supported tasks.
    - A string supports comma-separated values (``"value,timing"``).
    - An iterable input is normalized item by item.
    - Unsupported task names raise ``ValueError``.
    - Non-string, non-iterable inputs raise ``TypeError``.
    """

    if tasks is None:
        return list(VALID_MODEL_TASKS)

    if isinstance(tasks, str):
        parts = [part.strip().lower() for part in tasks.split(",") if part.strip()]
    else:
        if not isinstance(tasks, Iterable):
            raise TypeError(
                "tasks must be a comma-separated string, an iterable of task names, or None"
            )
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
