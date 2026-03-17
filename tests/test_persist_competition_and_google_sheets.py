from __future__ import annotations

import json
from pathlib import Path

import pytest

from floor.external.google_sheets import ExternalRecommendation, fetch_recommendations
from models import persist_competition_results as persist_results


class _FakeResp:
    def __init__(self, payload: str) -> None:
        self._payload = payload.encode("utf-8")

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


def test_fetch_recommendations_returns_empty_on_none_url() -> None:
    assert fetch_recommendations(None) == []


def test_fetch_recommendations_returns_empty_when_url_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_: object, **__: object):
        raise RuntimeError("unavailable")

    monkeypatch.setattr("floor.external.google_sheets.urlopen", _boom)

    assert fetch_recommendations("http://example") == []


def test_fetch_recommendations_requires_expected_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "floor.external.google_sheets.urlopen",
        lambda *_args, **_kwargs: _FakeResp("symbol,action\nAAPL,BUY\n"),
    )

    assert fetch_recommendations("http://example") == []


def test_fetch_recommendations_parses_and_normalizes(monkeypatch: pytest.MonkeyPatch) -> None:
    csv_data = "symbol,action,confidence,note\naapl,buy,0.75,first\nmsft,sell,0.25,second\n"
    monkeypatch.setattr(
        "floor.external.google_sheets.urlopen",
        lambda *_args, **_kwargs: _FakeResp(csv_data),
    )

    rows = fetch_recommendations("http://example")

    assert rows == [
        ExternalRecommendation(symbol="AAPL", action="BUY", confidence=0.75, note="first"),
        ExternalRecommendation(symbol="MSFT", action="SELL", confidence=0.25, note="second"),
    ]


def test_persist_competition_run_skips_bad_candidates_and_non_lists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    db_path = tmp_path / "app.sqlite"

    (models_dir / "h1_competition.json").write_text(
        json.dumps(
            {
                "horizon": "H1",
                "version": "v1",
                "selected_model_id": "m2",
                "candidates": [
                    {"model_id": "m1", "score": 0.2},
                    "invalid-row",
                    {"model_id": "m2", "score": 0.9},
                ],
            }
        ),
        encoding="utf-8",
    )
    (models_dir / "h4_competition.json").write_text(
        json.dumps({"horizon": "H4", "candidates": "invalid"}),
        encoding="utf-8",
    )

    persisted: list[tuple[Path, str, dict]] = []

    def _fake_persist(path: Path, table: str, row: dict) -> None:
        persisted.append((path, table, row))

    monkeypatch.setattr(persist_results, "persist_payload", _fake_persist)

    count = persist_results.run(models_dir=models_dir, db_path=db_path)

    assert count == 2
    assert len(persisted) == 2
    assert all(item[0] == db_path for item in persisted)
    assert all(item[1] == "model_competition" for item in persisted)
    assert persisted[0][2]["is_champion"] is False
    assert persisted[1][2]["is_champion"] is True
    assert persisted[1][2]["horizon"] == "H1"
    assert "source_artifact" in persisted[0][2]


def test_persist_competition_main_prints_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    db_path = tmp_path / "app.sqlite"

    (models_dir / "h1_competition.json").write_text(
        json.dumps({"selected_model_id": "m1", "candidates": [{"model_id": "m1"}]}),
        encoding="utf-8",
    )

    monkeypatch.setattr("sys.argv", ["persist_competition_results", "--models-dir", str(models_dir), "--db", str(db_path)])
    monkeypatch.setattr(persist_results, "persist_payload", lambda *_args, **_kwargs: None)

    persist_results.main()

    assert "persisted_model_competition_rows=1" in capsys.readouterr().out
