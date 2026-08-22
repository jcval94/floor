from __future__ import annotations

from pathlib import Path

import pytest

from floor.config import RuntimeConfig
from floor.pipeline import canonical_intraday_cycle as canonical


def test_forecast_batch_guard_accepts_exact_complete_batch() -> None:
    canonical._validate_forecast_batch(
        forecasts=[{"symbol": "AAPL"}, {"symbol": "MSFT"}],
        blocked=[],
        expected_symbols=["AAPL", "MSFT"],
    )


def test_forecast_batch_guard_rejects_blocked_partial_or_duplicate_output() -> None:
    with pytest.raises(RuntimeError, match="blocked=AAPL"):
        canonical._validate_forecast_batch(
            forecasts=[{"symbol": "MSFT"}],
            blocked=[{"symbol": "AAPL", "reason": "missing model"}],
            expected_symbols=["AAPL", "MSFT"],
        )

    with pytest.raises(RuntimeError, match="missing=MSFT"):
        canonical._validate_forecast_batch(
            forecasts=[{"symbol": "AAPL"}],
            blocked=[],
            expected_symbols=["AAPL", "MSFT"],
        )

    with pytest.raises(RuntimeError, match="duplicates=AAPL"):
        canonical._validate_forecast_batch(
            forecasts=[{"symbol": "AAPL"}, {"symbol": "AAPL"}],
            blocked=[],
            expected_symbols=["AAPL"],
        )


def _patch_minimal_cycle(monkeypatch: pytest.MonkeyPatch, *, blocked: bool = False) -> list[str]:
    written_paths: list[str] = []

    monkeypatch.setattr(canonical, "_latest_feature_rows", lambda _cfg, _symbols: [{"symbol": "AAPL"}])
    monkeypatch.setattr(canonical, "_validate_feature_rows", lambda _rows: None)
    monkeypatch.setattr(canonical, "_log_model_registry_preflight", lambda _cfg: None)
    monkeypatch.setattr(canonical, "_model_input_snapshot", lambda _row, _ai: {})
    monkeypatch.setattr(canonical, "_model_output_snapshot", lambda _row: {})
    monkeypatch.setattr(
        canonical,
        "run_forecast_pipeline",
        lambda **kwargs: {
            "dataset_forecasts": [] if blocked else [{"symbol": "AAPL", "model_version": "v1"}],
            "blocked_list": [{"symbol": "AAPL", "reason": "missing model"}] if blocked else [],
            "received_ai_map": kwargs["ai_by_symbol"],
        },
    )
    monkeypatch.setattr(
        canonical,
        "_prediction_payloads",
        lambda _row, event: [
            (
                "d1",
                {
                    "floor_value": 100.0,
                    "ceiling_value": 110.0,
                    "floor_time_bucket": "OPEN",
                    "ceiling_time_bucket": "CLOSE",
                    "floor_time_probability": 0.8,
                    "ceiling_time_probability": 0.8,
                    "confidence_score": 0.8,
                    "expected_return": 0.02,
                    "expected_range": 10.0,
                    "event_type": event,
                    "emit_signal": True,
                    "m3_payload": {},
                },
            )
        ],
    )
    monkeypatch.setattr(canonical, "_validate_prediction_payload", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(canonical, "_signal_from_prediction", lambda *_args, **_kwargs: {"action": "BUY"})
    monkeypatch.setattr(canonical, "reconcile_predictions", lambda _data_dir: {"pending": 0})
    monkeypatch.setattr(
        canonical,
        "append_jsonl",
        lambda path, _record: written_paths.append(str(path)),
    )
    return written_paths


def test_canonical_cycle_is_signal_only_and_does_not_write_orders(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    written_paths = _patch_minimal_cycle(monkeypatch)
    cfg = RuntimeConfig(root_dir=tmp_path, data_dir=tmp_path / "data")

    canonical.run_intraday_cycle("OPEN", ["AAPL"], cfg)

    assert any("/predictions/" in path for path in written_paths)
    assert any("/signals/" in path for path in written_paths)
    assert all("/orders/" not in path for path in written_paths)


def test_canonical_cycle_fails_before_any_persistence_when_models_are_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    written_paths = _patch_minimal_cycle(monkeypatch, blocked=True)
    cfg = RuntimeConfig(root_dir=tmp_path, data_dir=tmp_path / "data")

    with pytest.raises(RuntimeError, match="blocked=AAPL"):
        canonical.run_intraday_cycle("OPEN", ["AAPL"], cfg)

    assert written_paths == []


def test_floor_main_routes_run_cycle_to_canonical_runtime() -> None:
    from floor import main as floor_main

    assert floor_main.run_intraday_cycle.__module__ == "floor.pipeline.canonical_intraday_cycle"
