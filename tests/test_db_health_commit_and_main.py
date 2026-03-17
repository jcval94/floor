from __future__ import annotations

import sqlite3
from pathlib import Path
import pytest

from floor import db_health, main as floor_main
from floor.config import RuntimeConfig
from storage.commit_history import commit_if_changed


def _create_health_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE predictions (
                symbol TEXT,
                as_of TEXT,
                event_type TEXT,
                horizon TEXT
            )
            """
        )
        for table in sorted(db_health.REQUIRED_TABLES - {"predictions"}):
            conn.execute(f"CREATE TABLE {table} (id INTEGER)")


class _Completed:
    def __init__(self, stdout: str = "") -> None:
        self.stdout = stdout


def test_db_health_run_happy_path(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_path = tmp_path / "ok.sqlite"
    _create_health_db(db_path)

    result = db_health.run(db_path)

    out = capsys.readouterr().out
    assert result == 0
    assert "integrity_check=ok" in out
    assert "prediction_duplicate_keys=0" in out
    assert "journal_mode=" in out


def test_db_health_run_missing_db(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    db_path = tmp_path / "missing.sqlite"

    result = db_health.run(db_path)

    assert result == 2
    assert f"ERROR: db_not_found path={db_path}" in capsys.readouterr().out


def test_db_health_run_missing_required_tables(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_path = tmp_path / "missing_tables.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE predictions (symbol TEXT, as_of TEXT, event_type TEXT, horizon TEXT)")

    result = db_health.run(db_path)

    assert result == 1
    assert "required_missing=" in capsys.readouterr().out


def test_db_health_main_exits_with_return_code(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "ok.sqlite"
    _create_health_db(db_path)
    monkeypatch.setattr("sys.argv", ["db_health", "--db", str(db_path)])

    with pytest.raises(SystemExit) as exc:
        db_health.main()

    assert exc.value.code == 0


def test_db_health_run_returns_error_on_failed_integrity(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "broken.sqlite"
    db_path.write_text("placeholder", encoding="utf-8")

    class _Row:
        def __getitem__(self, _: int) -> str:
            return "not ok"

    class _Conn:
        def __enter__(self) -> "_Conn":
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def execute(self, query: str):
            assert "integrity_check" in query
            return type("_Cur", (), {"fetchone": lambda self: _Row()})()

    monkeypatch.setattr(db_health, "_connect", lambda _: _Conn())

    assert db_health.run(db_path) == 1


def test_commit_if_changed_handles_no_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def _fake_run(cmd: list[str], **_: object) -> _Completed:
        calls.append(cmd)
        return _Completed(stdout="")

    monkeypatch.setattr("subprocess.run", _fake_run)

    result = commit_if_changed("msg", ["tests"])

    assert result == {"committed": False, "reason": "no_changes"}
    assert calls == [["git", "status", "--porcelain", "--", "tests"]]


def test_commit_if_changed_uses_dot_when_paths_not_provided(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def _fake_run(cmd: list[str], **_: object) -> _Completed:
        calls.append(cmd)
        return _Completed(stdout="")

    monkeypatch.setattr("subprocess.run", _fake_run)

    result = commit_if_changed("msg")

    assert result == {"committed": False, "reason": "no_changes"}
    assert calls[0] == ["git", "status", "--porcelain", "--", "."]


def test_commit_if_changed_commits_when_dirty(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def _fake_run(cmd: list[str], **_: object) -> _Completed:
        calls.append(cmd)
        if cmd[:3] == ["git", "status", "--porcelain"]:
            return _Completed(stdout=" M tests/file.py\n")
        if cmd[:2] == ["git", "commit"]:
            return _Completed(stdout="[main 123] msg")
        return _Completed(stdout="")

    monkeypatch.setattr("subprocess.run", _fake_run)

    result = commit_if_changed("msg", ["tests"])

    assert result["committed"] is True
    assert result["reason"] == "changes_committed"
    assert "[main 123]" in result["stdout"]
    assert calls[1] == ["git", "add", "--", "tests"]
    assert calls[2] == ["git", "commit", "-m", "msg"]


def test_floor_main_run_cycle_without_session(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("sys.argv", ["floor", "run-cycle"])
    monkeypatch.setattr(floor_main.RuntimeConfig, "from_env", staticmethod(lambda: RuntimeConfig()))
    monkeypatch.setattr(floor_main, "nearest_event_type", lambda: None)

    floor_main.main()

    assert "No market session today; skipping run-cycle" in capsys.readouterr().out


def test_floor_main_run_cycle_uses_symbols_arg(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, object] = {}

    monkeypatch.setattr("sys.argv", ["floor", "run-cycle", "--event", "OPEN", "--symbols", "aapl, msft "])
    monkeypatch.setattr(floor_main.RuntimeConfig, "from_env", staticmethod(lambda: RuntimeConfig()))
    monkeypatch.setattr(floor_main, "run_intraday_cycle", lambda **kwargs: called.update(kwargs))

    floor_main.main()

    assert called["event_type"] == "OPEN"
    assert called["symbols"] == ["AAPL", "MSFT"]


def test_floor_main_run_cycle_uses_universe_when_symbols_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, object] = {}

    monkeypatch.setattr("sys.argv", ["floor", "run-cycle", "--event", "OPEN"])
    monkeypatch.setattr(floor_main.RuntimeConfig, "from_env", staticmethod(lambda: RuntimeConfig()))
    monkeypatch.setattr(floor_main, "parse_universe_yaml", lambda _: ["SPY", "QQQ"])
    monkeypatch.setattr(floor_main, "run_intraday_cycle", lambda **kwargs: called.update(kwargs))

    floor_main.main()

    assert called["event_type"] == "OPEN"
    assert called["symbols"] == ["SPY", "QQQ"]


def test_floor_main_subcommands_and_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(floor_main.RuntimeConfig, "from_env", staticmethod(lambda: RuntimeConfig()))

    invoked: list[str] = []
    monkeypatch.setattr(floor_main, "run_training_review", lambda **_: invoked.append("review"))
    monkeypatch.setattr(floor_main, "reconcile_predictions", lambda *_: invoked.append("reconcile"))

    def _boom(*_: object, **__: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(floor_main, "build_dashboard_snapshot", _boom)

    monkeypatch.setattr("sys.argv", ["floor", "review-training"])
    floor_main.main()

    monkeypatch.setattr("sys.argv", ["floor", "reconcile-predictions"])
    floor_main.main()

    monkeypatch.setattr("sys.argv", ["floor", "build-site"])
    with pytest.raises(RuntimeError, match="boom"):
        floor_main.main()

    assert invoked == ["review", "reconcile"]
