#!/usr/bin/env python3
"""Audit two missing official CLOSE sessions locally before any remote publication.

This tool is deliberately *not* a normal EOD fallback. It consumes a pinned,
explicitly authorized recovery request. All writes are local in the disposable
Actions runner; the workflow promotes its results only after both CLOSEs pass.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from floor.calendar import is_early_close, is_market_session
from floor.universe import parse_universe_yaml
from league.market_features import build_league_market_snapshot
from utils.market_data_guard import validate_market_data_freshness

ET = ZoneInfo("America/New_York")


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"Expected a JSON object at {path}")
    return value


def _next_session(day: date) -> date:
    candidate = day + timedelta(days=1)
    while not is_market_session(candidate):
        candidate += timedelta(days=1)
    return candidate


def validate_request(request: dict, now: datetime | None = None) -> list[str]:
    """Strictly one adjacent missed-session pair, before the next market open."""
    if (
        request.get("mode") != "historical_eod_backfill"
        or request.get("authorization") != "CONFIRM_TWO_SESSION_AUDITED_BACKFILL"
    ):
        raise RuntimeError("Audited backfill requires explicit request authorization")
    sessions = request.get("target_sessions")
    if not isinstance(sessions, list) or len(sessions) != 2 or not all(
        isinstance(x, str) for x in sessions
    ):
        raise RuntimeError("Exactly two ordered historical CLOSE sessions are required")
    a, b = (date.fromisoformat(session) for session in sessions)
    if not is_market_session(a) or not is_market_session(b) or _next_session(a) != b:
        raise RuntimeError("Requested historical EOD sessions are not adjacent NYSE days")
    origin = date.fromisoformat(str(request.get("source_last_session") or ""))
    if not is_market_session(origin) or _next_session(origin) != a:
        raise RuntimeError("Backfill origin must be immediately before its first target")
    current = (now or datetime.now(ET)).astimezone(ET)
    close_at = datetime.combine(
        b, time(13, 0) if is_early_close(b) else time(16, 0), tzinfo=ET
    )
    next_open = datetime.combine(_next_session(b), time(9, 30), tzinfo=ET)
    if current < close_at + timedelta(minutes=20) or current >= next_open:
        raise RuntimeError(
            "Audited backfill window closed or daily bar not complete: "
            f"target_close={close_at.isoformat()} next_open={next_open.isoformat()}"
        )
    return [a.isoformat(), b.isoformat()]


def _cmd(*parts: str) -> None:
    print("AUDIT_EXEC:", " ".join(parts), flush=True)
    subprocess.run(parts, check=True, env={**os.environ, "LIVE_TRADING_ENABLED": "false"})


def _state(data_dir: Path, league_id: str) -> dict:
    return _read(data_dir / "metrics" / "strategy_league" / "runs" / league_id / "state.json")


def replay(request_path: Path, *, data_dir: Path = Path("data")) -> dict:
    request = _read(request_path)
    sessions = validate_request(request)
    league_id = str(_read(Path("config/strategy_league.json"))["league_id"])
    old = _state(data_dir, league_id)
    expected_origin = str(request["source_last_session"])
    if str(old.get("last_session") or "") != expected_origin:
        raise RuntimeError(
            "Refusing historical replay from a different official state: "
            f"expected={expected_origin} found={old.get('last_session')}"
        )
    original_hash = str(old.get("last_hash") or "")
    if not original_hash or int(old.get("session_count", 0)) < 1:
        raise RuntimeError("Historical replay requires a hash-bound official league baseline")
    for session in sessions:
        marker = data_dir / "snapshots" / "workflow_runs" / f"eod_{session}_CLOSE.json"
        if marker.exists():
            raise RuntimeError(f"Refusing to rewrite an already completed EOD: {session}")

    # Only fresh, real completed NYSE daily bars are acceptable. A newer bar
    # may coexist in the archive, but prediction/league inputs are time-scoped.
    symbols = sorted(set(parse_universe_yaml(Path("config/universe.yaml")) + ["SPY"]))
    audit_dir = data_dir / "metrics" / "strategy_league" / "recovery_audits"
    audit_dir.mkdir(parents=True, exist_ok=True)
    ledger: list[dict] = []
    initial_sessions = int(old["session_count"])

    for index, session in enumerate(sessions, 1):
        day = date.fromisoformat(session)
        close_at = datetime.combine(
            day, time(13, 0) if is_early_close(day) else time(16, 0), tzinfo=ET
        )
        complete_at = close_at + timedelta(minutes=20)
        validate_market_data_freshness(
            data_dir / "market" / "market_data.sqlite",
            symbols, now=complete_at, required_session=day, max_stale_sessions=0,
        )
        _cmd(
            "python", "-m", "floor.main", "run-cycle", "--event", "CLOSE",
            "--checkpoint-at", close_at.isoformat(),
            "--required-market-session", session,
        )
        snap = build_league_market_snapshot(
            data_dir / "market" / "market_data.sqlite",
            data_dir / "persistence" / "app.sqlite",
            [s for s in symbols if s != "SPY"],
            benchmark_symbol="SPY", required_market_session=session,
        )
        if snap.get("status") != "OK" or snap.get("session") != session:
            raise RuntimeError(
                f"Historical {session} forecast/market cohort invalid: "
                f"status={snap.get('status')} session={snap.get('session')} "
                f"complete_symbols={len(snap.get('complete_symbols') or [])}"
            )
        _cmd(
            "python", "-m", "league.live_snapshot", "export-base",
            "--data-dir", str(data_dir),
            "--league-config", "config/strategy_league.json",
            "--output", str(data_dir / "metrics" / "strategy_league" / "eod_preclose_base.json"),
        )
        _cmd(
            "python", "-m", "league.shadow_portfolio",
            "--base", str(data_dir / "metrics" / "strategy_league" / "eod_preclose_base.json"),
            "--previous-snapshot", str(data_dir / "metrics" / "strategy_league" / "live_snapshot.json"),
            "--league-config", "config/strategy_league.json",
            "--output", str(data_dir / "metrics" / "strategy_league" / "live_snapshot.json"),
            "--range", "5d", "--interval", "5m",
            "--checkpoint-at", close_at.isoformat(),
            "--authoritative-eod-replay",
        )
        shadow = _read(data_dir / "metrics" / "strategy_league" / "live_snapshot.json")
        if (
            shadow.get("market_session") != session
            or shadow.get("shadow_evidence_complete") is not True
            or shadow.get("open_fills_applied") is not True
        ):
            raise RuntimeError(f"Historical shadow evidence incomplete for {session}")
        _cmd(
            "python", "-m", "league.run_eod",
            "--data-dir", str(data_dir),
            "--league-config", "config/strategy_league.json",
            "--strategies-config", "config/strategies.yaml",
            "--universe", "config/universe.yaml",
            "--required-market-session", session,
        )
        result_path = audit_dir / f"shadow_eod_{session}.json"
        _cmd(
            "python", "-m", "league.shadow_reconcile",
            "--shadow", str(data_dir / "metrics" / "strategy_league" / "live_snapshot.json"),
            "--history", str(
                data_dir / "metrics" / "strategy_league" / "runs" / league_id / "history.jsonl"
            ),
            "--base", str(data_dir / "metrics" / "strategy_league" / "eod_preclose_base.json"),
            "--market-db", str(data_dir / "market" / "market_data.sqlite"),
            "--league-config", "config/strategy_league.json",
            "--output", str(result_path),
        )
        recon = _read(result_path)
        after = _state(data_dir, league_id)
        if (
            recon.get("status") != "RECONCILED"
            or recon.get("market_session") != session
            or recon.get("official_session") != session
            or after.get("last_session") != session
            or int(after.get("session_count", 0)) != initial_sessions + index
            or not str(after.get("last_hash") or "")
        ):
            raise RuntimeError(f"Historical close {session} did not reconcile or advance sequentially")
        # Expose the final accepted audit at the same canonical path as daily
        # EOD, while retaining immutable per-session recovery copies.
        (data_dir / "metrics" / "strategy_league" / "shadow_eod_reconciliation.json").write_text(
            result_path.read_text(encoding="utf-8"), encoding="utf-8"
        )
        record = {
            "session": session,
            "reconstructed": True,
            "scope": "audited_sequential_EOD_only",
            "source_baseline_hash": original_hash,
            "official_hash": after["last_hash"],
            "matched_trades": recon["matched_trade_count"],
            "explained_differences": recon.get("explained_difference_counts"),
            "unexplained_divergences": recon.get("divergences"),
            "forecast_symbol_count": len(snap["complete_symbols"]),
        }
        ledger.append(record)
        (audit_dir / f"provenance_{session}.json").write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        _cmd(
            "python", "-m", "utils.workflow_guards", "mark",
            "--kind", "eod", "--event", "CLOSE",
            "--session-day", session,
            "--checkpoint-at", close_at.isoformat(),
            "--data-dir", str(data_dir),
        )

    _cmd("python", "-m", "floor.main", "reconcile-predictions")
    _cmd("python", "-m", "floor.main", "build-site")
    _cmd(
        "python", "-m", "league.live_snapshot", "export-base",
        "--data-dir", str(data_dir),
        "--league-config", "config/strategy_league.json",
        "--output", str(data_dir / "metrics" / "strategy_league" / "live_base.json"),
    )
    _cmd(
        "python", "-m", "league.experiment_observation",
        "--data-dir", str(data_dir), "--league-config", "config/strategy_league.json",
    )
    final = _state(data_dir, league_id)
    if final.get("last_session") != sessions[-1]:
        raise RuntimeError("Final historical state session mismatch")
    summary = {
        "status": "RECONCILED_AWAITING_ATOMIC_PUBLICATION",
        "reconstructed": True,
        "sessions": sessions,
        "initial_official_hash": original_hash,
        "final_official_hash": final["last_hash"],
        "session_count": final["session_count"],
        "events": ledger,
    }
    (audit_dir / "backfill_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    out_file = os.getenv("GITHUB_OUTPUT")
    if out_file:
        with open(out_file, "a", encoding="utf-8") as f:
            f.write(f"final_session={sessions[-1]}\n")
            f.write(
                "final_checkpoint_at="
                + datetime.combine(
                    date.fromisoformat(sessions[-1]),
                    time(13) if is_early_close(date.fromisoformat(sessions[-1])) else time(16),
                    tzinfo=ET,
                ).isoformat()
                + "\n"
            )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--data-dir", default=Path("data"), type=Path)
    args = parser.parse_args()
    print(json.dumps(replay(args.request, data_dir=args.data_dir), indent=2))


if __name__ == "__main__":
    main()
