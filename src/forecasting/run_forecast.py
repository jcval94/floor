from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from floor.external.google_sheets import fetch_recommendations
from forecasting.generate_forecasts import generate_forecasts
from forecasting.rank_opportunities import rank_opportunities


def _load_market(path: Path) -> list[dict]:
    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    if path.suffix.lower() in {".json", ".jsonl"}:
        rows: list[dict] = []
        with path.open("r", encoding="utf-8") as f:
            if path.suffix.lower() == ".json":
                obj = json.loads(f.read())
                return obj if isinstance(obj, list) else obj.get("rows", [])
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows
    raise ValueError(f"Unsupported market input: {path}")


def _load_ai_map(path: Path | None, recommendations_csv_url: str | None) -> dict[str, dict]:
    by_symbol: dict[str, dict] = {}
    if path and path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload if isinstance(payload, list) else payload.get("rows", [])
        for r in rows:
            sym = str(r.get("symbol", "")).upper()
            if sym:
                by_symbol[sym] = r

    for ext in fetch_recommendations(recommendations_csv_url):
        by_symbol.setdefault(ext.symbol, {})
        by_symbol[ext.symbol].update(
            {
                "ai_action": ext.action,
                "ai_conviction": ext.confidence,
                "ai_consensus_score": ext.confidence,
                "ai_note": ext.note,
            }
        )
    return by_symbol


def run_forecast_pipeline(
    market_rows: list[dict],
    ai_by_symbol: dict[str, dict],
    session: str,
    as_of: datetime | None = None,
    model_registry_dir: Path | None = None,
) -> dict:
    as_of = as_of or datetime.now(tz=timezone.utc)
    generated = generate_forecasts(
        market_rows=market_rows,
        ai_by_symbol=ai_by_symbol,
        session=session,
        as_of=as_of,
        model_registry_dir=model_registry_dir,
    )
    ranked = rank_opportunities(generated["forecasts"], generated["blocked"])

    return {
        "as_of": as_of.isoformat(),
        "session": session,
        "dataset_forecasts": generated["forecasts"],
        **ranked,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run forecasting + signal integration pipeline")
    parser.add_argument("--market", required=True, help="Path to market rows (.csv|.json|.jsonl)")
    parser.add_argument("--ai-json", default=None, help="Optional AI signal JSON path")
    parser.add_argument("--ai-csv-url", default=None, help="Optional Google Sheet CSV URL")
    parser.add_argument("--session", default="OPEN", help="Session label")
    parser.add_argument("--out-dir", default="data/forecasts", help="Output base directory")
    args = parser.parse_args()

    market_rows = _load_market(Path(args.market))
    ai_map = _load_ai_map(Path(args.ai_json) if args.ai_json else None, args.ai_csv_url)
    as_of = datetime.now(tz=timezone.utc)

    out = run_forecast_pipeline(market_rows, ai_map, session=args.session, as_of=as_of)

    stamp = as_of.strftime("%Y%m%dT%H%M%SZ")
    version_dir = Path(args.out_dir) / f"{as_of.strftime('%Y-%m-%d')}_{args.session}_{stamp}"
    version_dir.mkdir(parents=True, exist_ok=True)

    (version_dir / "dataset_forecasts.json").write_text(
        json.dumps({"rows": out["dataset_forecasts"], "as_of": out["as_of"], "session": out["session"]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (version_dir / "top_opportunities.json").write_text(json.dumps(out["top_opportunities"], ensure_ascii=False, indent=2), encoding="utf-8")
    (version_dir / "low_confidence_list.json").write_text(json.dumps(out["low_confidence_list"], ensure_ascii=False, indent=2), encoding="utf-8")
    (version_dir / "blocked_list.json").write_text(json.dumps(out["blocked_list"], ensure_ascii=False, indent=2), encoding="utf-8")
    (version_dir / "canonical_strategy_output.json").write_text(json.dumps(out["canonical_strategy_output"], ensure_ascii=False, indent=2), encoding="utf-8")
    (version_dir / "dashboard_output.json").write_text(json.dumps(out["human_friendly_dashboard"], ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({"output_dir": str(version_dir), "n_forecasts": len(out["dataset_forecasts"]), "n_blocked": len(out["blocked_list"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
