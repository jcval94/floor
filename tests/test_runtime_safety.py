from __future__ import annotations

from pathlib import Path

import pytest

from floor.config import RuntimeConfig
from floor.external import google_sheets


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self._text = text

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return self._text.encode("utf-8")


def test_runtime_config_hard_blocks_live_trading() -> None:
    with pytest.raises(RuntimeError, match="LIVE trading is blocked"):
        RuntimeConfig(live_trading_enabled=True)


def test_runtime_config_allows_paper_mode() -> None:
    cfg = RuntimeConfig(
        root_dir=Path("."),
        data_dir=Path("data"),
        live_trading_enabled=False,
    )
    assert cfg.live_trading_enabled is False


def test_external_recommendations_drop_invalid_and_low_confidence_rows(monkeypatch) -> None:
    csv_text = """symbol,action,confidence,note
AAPL,BUY,0.95,valid
MSFT,SELL,0.79,too low
NVDA,EXPLODE,0.99,bad action
META,HOLD,1.20,out of range
ORCL,SELL,not-a-number,bad confidence
,BUY,0.99,missing symbol
"""
    monkeypatch.setattr(
        google_sheets,
        "urlopen",
        lambda *_args, **_kwargs: _FakeResponse(csv_text),
    )

    rows = google_sheets.fetch_recommendations(
        "https://example.invalid/recommendations.csv"
    )

    assert len(rows) == 1
    assert rows[0].symbol == "AAPL"
    assert rows[0].action == "BUY"
    assert rows[0].confidence == 0.95


def test_external_recommendations_require_expected_schema(monkeypatch) -> None:
    monkeypatch.setattr(
        google_sheets,
        "urlopen",
        lambda *_args, **_kwargs: _FakeResponse("symbol,action\nAAPL,BUY\n"),
    )

    assert (
        google_sheets.fetch_recommendations(
            "https://example.invalid/recommendations.csv"
        )
        == []
    )
