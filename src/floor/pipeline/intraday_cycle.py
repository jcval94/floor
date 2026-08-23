"""Deprecated intraday compatibility surface.

The only supported runtime is ``floor.pipeline.canonical_intraday_cycle``.
This module intentionally keeps a handful of helper imports for old callers,
but all legacy execution/order/fallback entry points fail closed.
"""

from __future__ import annotations

from typing import Any

from floor.pipeline.prediction_runtime import (
    _latest_feature_rows,
    _log_model_registry_preflight,
    _model_input_snapshot,
    _model_output_snapshot,
    _prediction_payloads,
    _signal_from_prediction,
    _to_float,
    _to_optional_float,
    _validate_feature_rows,
    _validate_prediction_payload,
)

LEGACY_RUNTIME_ERROR = (
    "Legacy intraday cycle is disabled because it allowed external signal overrides, "
    "synthetic forecast fallback and direct qty=1 order creation. Use "
    "floor.pipeline.canonical_intraday_cycle.run_intraday_cycle instead."
)


def run_intraday_cycle(*_args: Any, **_kwargs: Any) -> None:
    raise RuntimeError(LEGACY_RUNTIME_ERROR)


def maybe_build_order(*_args: Any, **_kwargs: Any) -> None:
    raise RuntimeError(LEGACY_RUNTIME_ERROR)


def _fallback_forecasts_from_blocked(*_args: Any, **_kwargs: Any) -> list[dict]:
    raise RuntimeError(LEGACY_RUNTIME_ERROR)


__all__ = [
    "LEGACY_RUNTIME_ERROR",
    "_fallback_forecasts_from_blocked",
    "_latest_feature_rows",
    "_log_model_registry_preflight",
    "_model_input_snapshot",
    "_model_output_snapshot",
    "_prediction_payloads",
    "_signal_from_prediction",
    "_to_float",
    "_to_optional_float",
    "_validate_feature_rows",
    "_validate_prediction_payload",
    "maybe_build_order",
    "run_intraday_cycle",
]
