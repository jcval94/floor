from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from floor.universe import parse_universe_yaml
from storage.market_db import load_daily_bars

logger = logging.getLogger(__name__)


def build_rows_from_db(db_path: Path, universe_path: Path, benchmark_symbol: str = "SPY") -> list[dict]:
    logger.info("[etl:training-rows] loading universe path=%s", universe_path)
    symbols = parse_universe_yaml(universe_path)
    benchmark_symbol = benchmark_symbol.upper()
    all_symbols = sorted(set(symbols + [benchmark_symbol]))
    logger.info("[etl:training-rows] reading bars from db=%s symbols=%s", db_path, len(all_symbols))

    try:
        bars = load_daily_bars(db_path, all_symbols)
    except Exception as exc:
        logger.exception("[etl:training-rows] failed reading market db=%s error=%s", db_path, exc)
        raise

    by_symbol: dict[str, list[dict]] = {}
    for row in bars:
        try:
            by_symbol.setdefault(str(row["symbol"]).upper(), []).append(row)
        except Exception as exc:
            logger.warning("[etl:training-rows] skipping bad row=%s error=%s", row, exc)

    benchmark_close_by_ts = {r["timestamp"]: float(r["close"]) for r in by_symbol.get(benchmark_symbol, [])}

    output: list[dict] = []
    for symbol in symbols:
        for row in by_symbol.get(symbol, []):
            try:
                ts = row["timestamp"]
                output.append(
                    {
                        "timestamp": ts,
                        "symbol": symbol,
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "volume": float(row["volume"]),
                        "benchmark_close": benchmark_close_by_ts.get(ts),
                        "ai_conviction": None,
                        "ai_floor_d1": None,
                        "ai_ceiling_d1": None,
                        "ai_floor_w1": None,
                        "ai_ceiling_w1": None,
                        "ai_floor_q1": None,
                        "ai_ceiling_q1": None,
                        "ai_floor_m3": None,
                        "ai_conviction_long": None,
                        "ai_recency_long": None,
                        "ai_consensus_score": None,
                    }
                )
            except Exception as exc:
                logger.warning("[etl:training-rows] failed converting symbol=%s row=%s error=%s", symbol, row, exc)

    output.sort(key=lambda x: (x["timestamp"], x["symbol"]))
    logger.info("[etl:training-rows] built rows=%s", len(output))
    if output:
        logger.info("[etl:training-rows] sample first=%s", output[0])
        logger.info("[etl:training-rows] sample last=%s", output[-1])
    return output


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    parser = argparse.ArgumentParser(description="Build raw training rows from in-repo market DB")
    parser.add_argument("--db", default="data/market/market_data.sqlite")
    parser.add_argument("--universe", default="config/universe.yaml")
    parser.add_argument("--output", default="data/training/yahoo_market_rows.jsonl")
    parser.add_argument("--benchmark", default="SPY")
    args = parser.parse_args()

    try:
        rows = build_rows_from_db(Path(args.db), Path(args.universe), benchmark_symbol=args.benchmark)

        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

        logger.info("[etl:training-rows] wrote output path=%s rows=%s", out, len(rows))
        print(json.dumps({"rows": len(rows), "output": str(out)}, ensure_ascii=False, indent=2))
    except Exception as exc:
        logger.exception("[etl:training-rows] fatal CLI error: %s", exc)
        raise


if __name__ == "__main__":
    main()
