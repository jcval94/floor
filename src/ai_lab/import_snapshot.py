"""Operator-only import of a read-only market DB from a verified FLOOR archive.

Daily bar timestamps denote session OPEN in the existing DB. These OHLC features
are conservatively materialized at 23:59 UTC, never at the source open timestamp.
This is retrospective reconstruction, not verified historical publication times.
"""

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import tarfile
from datetime import datetime
from ai_lab.store import Store
from features.feature_builder import build_features
from features.labels import build_labels

FEATURES = [
    "ret_lag_1",
    "momentum_20",
    "rolling_vol_20",
    "relative_volume_20",
    "atr_14",
]


def import_market(
    root, db, archive_hash, source_run_id, symbols=("SPY", "GOOGL", "MSFT")
):
    with sqlite3.connect("file:" + str(db.resolve()) + "?mode=ro", uri=True) as c:
        c.row_factory = sqlite3.Row
        rows = [
            dict(r)
            for r in c.execute(
                "SELECT * FROM daily_bars WHERE symbol IN (?,?,?) ORDER BY ts_utc,symbol",
                symbols,
            )
        ]
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    import exchange_calendars as xc

    calendar = xc.get_calendar("XNYS")
    accepted, excluded = [], []
    for r in rows:
        session = r["ts_utc"][:10]
        if not calendar.is_session(session):
            raise ValueError("NONSESSION_MARKET_BAR")
        close = calendar.session_close(session).to_pydatetime()
        fetched = datetime.fromisoformat(r["fetched_at_utc"])
        if fetched < close:
            excluded.append(
                {
                    "symbol": r["symbol"],
                    "session": session,
                    "reason": "fetched_before_close",
                }
            )
            continue
        # Conservative decision time after daily OHLC is complete, but does not
        # pretend the revised value is a historical vintage observation.
        r["timestamp"] = session + "T23:59:00+00:00"
        r["available_at"] = r["timestamp"]
        accepted.append(r)
    rows = accepted
    for symbol in symbols:
        days = sorted(r["timestamp"][:10] for r in rows if r["symbol"] == symbol)
        expected = [
            str(d.date()) for d in calendar.sessions_in_range(days[0], days[-1])
        ]
        if days != expected:
            raise ValueError("MISSING_OR_DUPLICATE_MARKET_SESSIONS")
    rows = build_labels(build_features(rows))
    prepared = []
    for r in rows:
        if not r.get("horizon_complete_w1") or any(r.get(f) is None for f in FEATURES):
            continue
        ts = r["timestamp"]
        prepared.append(
            {
                k: r[k]
                for k in [
                    "symbol",
                    "timestamp",
                    "available_at",
                    "close",
                    "floor_w1",
                    "horizon_complete_w1",
                ]
                + FEATURES
            }
            | {
                "target_end_date_w1": str(r["target_end_date_w1"])[:10]
                + "T23:59:00+00:00",
                "feature_available_at": {f: ts for f in FEATURES},
            }
        )
    manifest = {
        "source": "FLOOR runtime-state-v1/market_data.sqlite",
        "archive_sha256": archive_hash,
        "source_run_id": source_run_id,
        "target": "floor_w1",
        "horizon_sessions": 5,
        "features": FEATURES,
        "temporal_evidence": "retrospective_reconstructed",
        "calendar": "XNYS/exchange-calendars-4.13.2",
        "excluded_incomplete_bars": excluded,
        "as_of": max(r["timestamp"] for r in prepared),
        "source_db_sha256": hashlib.file_digest(db.open("rb"), "sha256").hexdigest(),
        "warnings": [
            "Daily close known after session, never at stored session open.",
            "Fetched/revised Yahoo history is not point-in-time vintage.",
            "Current survivor universe only, not unbiased market sampling.",
            "Imported Release is historical, not current EOD authority.",
        ],
    }
    return Store(root).register(prepared, manifest)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--root", required=True, type=Path)
    p.add_argument("--db", required=True, type=Path)
    p.add_argument("--archive-hash", required=True)
    p.add_argument("--source-run-id", required=True)
    p.add_argument("--archive", required=True, type=Path)
    a = p.parse_args()
    actual = hashlib.file_digest(a.archive.open("rb"), "sha256").hexdigest()
    if actual != a.archive_hash:
        raise SystemExit("ARCHIVE_HASH_MISMATCH")
    with tarfile.open(a.archive) as archive:
        member = archive.getmember("data/market/market_data.sqlite")
        if not member.isfile() or member.size > 64 * 1024**2:
            raise SystemExit("INVALID_MARKET_MEMBER")
        source = archive.extractfile(member)
        if source is None:
            raise SystemExit("MISSING_MARKET_MEMBER")
        hasher = hashlib.sha256()
        with source:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)
        archived_hash = hasher.hexdigest()
    if archived_hash != hashlib.file_digest(a.db.open("rb"), "sha256").hexdigest():
        raise SystemExit("DATABASE_NOT_FROM_ARCHIVE")
    print(
        json.dumps(
            {"dataset_id": import_market(a.root, a.db, a.archive_hash, a.source_run_id)}
        )
    )
