from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BENCHMARKS = {"benchmark_spy", "benchmark_equal_weight"}
FOCUS_STRATEGY = "capital_allocation_challenger"


def _equity_with_drawdown(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    peak = 0.0
    out: list[dict[str, Any]] = []
    for point in points:
        nav = float(point.get("nav", point.get("equity", 0.0)) or 0.0)
        if nav <= 0:
            continue
        peak = max(peak, nav)
        drawdown = nav / peak - 1.0 if peak > 0 else 0.0
        out.append(
            {
                "session": point.get("session"),
                "nav": nav,
                "equity": nav,
                "drawdown": drawdown,
            }
        )
    return out


def build_strategy_report(tournament: dict[str, Any]) -> dict[str, Any]:
    if tournament.get("prospective_evidence") is not False:
        raise ValueError("retrospective report requires prospective_evidence=false")
    if tournament.get("future_data_used") is not False:
        raise ValueError("retrospective report refuses future_data_used=true")

    leaderboard = tournament.get("leaderboard")
    if not isinstance(leaderboard, dict):
        raise ValueError("tournament leaderboard missing")
    raw_rows = leaderboard.get("rows")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise ValueError("tournament leaderboard rows missing")

    rows = sorted(
        (dict(row) for row in raw_rows if isinstance(row, dict)),
        key=lambda row: float(row.get("return", 0.0) or 0.0),
        reverse=True,
    )
    for rank, row in enumerate(rows, start=1):
        strategy = str(row.get("strategy") or "")
        row["rank"] = rank
        row["member_type"] = "benchmark" if strategy in BENCHMARKS else "strategy"
        row["promotion_review_eligible"] = False

    by_id = {str(row.get("strategy")): row for row in rows}
    focus = by_id.get(FOCUS_STRATEGY)
    if focus is None:
        raise ValueError(f"focus strategy missing: {FOCUS_STRATEGY}")

    strategy_rows = [row for row in rows if row.get("member_type") == "strategy"]
    base_rows = [row for row in strategy_rows if row.get("strategy") != FOCUS_STRATEGY]
    spy = by_id.get("benchmark_spy")
    best_base = base_rows[0] if base_rows else None

    focus_return = float(focus.get("return", 0.0) or 0.0)
    spy_return = float(spy.get("return", 0.0) or 0.0) if spy is not None else None
    best_base_return = (
        float(best_base.get("return", 0.0) or 0.0) if best_base is not None else None
    )

    focus_curve = _equity_with_drawdown(
        list(focus.get("equity_curve", [])) if isinstance(focus.get("equity_curve"), list) else []
    )

    return {
        "schema_version": 2,
        "status": "RETROSPECTIVE_OK",
        "evidence_type": tournament.get(
            "evidence_type", "retrospective_point_in_time_capital_tournament"
        ),
        "prospective_evidence": False,
        "future_data_used": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "start_session": tournament.get("start_session"),
        "end_session": tournament.get("end_session"),
        "sessions": int(tournament.get("sessions", 0) or 0),
        "initial_nav_usd": float(tournament.get("initial_nav_usd", 0.0) or 0.0),
        "focus_strategy": FOCUS_STRATEGY,
        "equity_curve": focus_curve,
        "rows": rows,
        "summary": {
            "overall_leader": rows[0].get("strategy"),
            "overall_leader_return": float(rows[0].get("return", 0.0) or 0.0),
            "challenger_rank": int(focus.get("rank", 0) or 0),
            "challenger_return": focus_return,
            "challenger_vs_spy": (
                focus_return - spy_return if spy_return is not None else None
            ),
            "best_base_strategy": best_base.get("strategy") if best_base else None,
            "challenger_vs_best_base": (
                focus_return - best_base_return if best_base_return is not None else None
            ),
        },
        "methodology_note": (
            "Retrospective diagnostic point-in-time market-data replay. No future market data "
            "is used inside replay sessions. This is not prospective evidence or pure OOS proof; "
            "the run uses the champions/configuration available when the replay is executed."
        ),
    }


def publish_tournament_report(input_path: Path, output_path: Path) -> dict[str, Any]:
    tournament = json.loads(input_path.read_text(encoding="utf-8"))
    report = build_strategy_report(tournament)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert a capital tournament into the public retrospective strategy report"
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", default="data/reports/strategy.json")
    args = parser.parse_args()
    report = publish_tournament_report(Path(args.input), Path(args.output))
    print(
        json.dumps(
            {
                "status": report["status"],
                "start_session": report["start_session"],
                "end_session": report["end_session"],
                "sessions": report["sessions"],
                "leader": report["summary"]["overall_leader"],
                "challenger_rank": report["summary"]["challenger_rank"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
