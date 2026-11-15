"""Core SQLite contracts run even when optional MCP research extras are absent."""

import sqlite3

import pytest

from ai_lab.catalog import inspect_db
from ai_lab.schema_guard import check, compare


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "sample.sqlite"
    with sqlite3.connect(path) as c:
        c.executescript("""
        CREATE TABLE observations(symbol TEXT NOT NULL, session TEXT NOT NULL, value REAL,
            PRIMARY KEY(symbol, session));
        CREATE INDEX by_value ON observations(value);
        CREATE VIEW observed_values AS SELECT symbol,value FROM observations;
        """)
    return path


def test_data_changes_do_not_masquerade_as_schema_drift(database):
    baseline = inspect_db(database)
    with sqlite3.connect(database) as c:
        c.execute("INSERT INTO observations VALUES('FIXTURE','2026-01-01',3)")
    before = database.read_bytes()
    report = check([baseline], [database])
    assert report["ok"]
    assert report["databases"][0]["row_counts"] == {"observations": 1}
    assert database.read_bytes() == before
    assert baseline["sha256"] != report["databases"][0]["observed_database_sha256"]


@pytest.mark.parametrize(
    "change",
    [
        "ALTER TABLE observations ADD COLUMN available_at TEXT",
        "DROP INDEX by_value; CREATE INDEX by_value ON observations(session)",
        "DROP VIEW observed_values; CREATE VIEW observed_values AS SELECT session FROM observations",
        "CREATE TRIGGER reject_values BEFORE INSERT ON observations BEGIN SELECT RAISE(ABORT,'fixture'); END",
        "CREATE TABLE new_table(id INTEGER PRIMARY KEY)",
        "DROP TABLE observations",
    ],
)
def test_schema_changes_block_even_when_names_or_counts_match(database, change):
    baseline = inspect_db(database)
    with sqlite3.connect(database) as c:
        c.executescript(change)
    report = compare(baseline, inspect_db(database))
    assert not report["schema_matches"]
    assert report["expected_schema_sha256"] != report["observed_schema_sha256"]
    assert report["mutation_performed"] is False


def test_incomplete_inventory_or_legacy_index_metadata_is_rejected(database):
    baseline = inspect_db(database)
    with pytest.raises(ValueError, match="DATABASE_SET_MISMATCH"):
        check([], [])
    with pytest.raises(ValueError, match="DATABASE_SET_MISMATCH"):
        check([baseline], [])
    with pytest.raises(ValueError, match="DATABASE_SET_MISMATCH"):
        check([baseline], [database, database])
    with pytest.raises(ValueError, match="DUPLICATE_BASELINE_DATABASE"):
        check([baseline, baseline], [database])
    del baseline["tables"][0]["indexes"][0]["columns"]
    with pytest.raises(ValueError, match="BASELINE_SCHEMA_METADATA_INCOMPLETE"):
        compare(baseline, inspect_db(database))


def test_read_only_inspection_cannot_create_missing_database(tmp_path):
    missing = tmp_path / "missing.sqlite"
    with pytest.raises(sqlite3.OperationalError):
        inspect_db(missing)
    assert not missing.exists()
