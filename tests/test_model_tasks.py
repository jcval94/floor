from __future__ import annotations

import pytest

from models.tasks import normalize_model_tasks


def test_normalize_model_tasks_with_single_string() -> None:
    assert normalize_model_tasks("value") == ["value"]


def test_normalize_model_tasks_with_list_of_strings() -> None:
    assert normalize_model_tasks(["timing", "value", "timing"]) == ["timing", "value"]


def test_normalize_model_tasks_with_none_returns_default_tasks() -> None:
    assert normalize_model_tasks(None) == ["value", "timing"]


def test_normalize_model_tasks_with_empty_values_returns_default_tasks() -> None:
    assert normalize_model_tasks("") == ["value", "timing"]
    assert normalize_model_tasks([]) == ["value", "timing"]


def test_normalize_model_tasks_with_invalid_type_raises_useful_error() -> None:
    with pytest.raises(TypeError, match="tasks must be a comma-separated string"):
        normalize_model_tasks(123)  # type: ignore[arg-type]
