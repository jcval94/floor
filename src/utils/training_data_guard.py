from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from floor.universe import parse_universe_yaml
from utils.market_data_guard import validate_market_data_freshness


def validate_training_market_coverage(
    db_path: Path,
    universe_path: Path,
    *,
    benchmark: str = "SPY",
    min_rows_per_symbol: int = 60,
    max_age_days: int = 7,
) -> dict[str, object]:
    if min_rows_per_symbol <= 0:
        raise ValueError("min_rows_per_symbol must be > 0")

    symbols = parse_universe_yaml(universe_path)
    benchmark_symbol = benchmark.strip().upper()
    if benchmark_symbol and benchmark_symbol not in symbols:
        symbols.append(benchmark_symbol)
    requested = sorted({symbol.strip().upper() for symbol in symbols if symbol.strip()})
    if not requested:
        raise RuntimeError("Training data validation refused: universe is empty")

    freshness = validate_market_data_freshness(
        db_path,
        requested,
        max_age_days=max_age_days,
    )

    placeholders = ",".join("?" for _ in requested)
    query = f"""
        SELECT symbol, COUNT(*)
        FROM daily_bars
        WHERE symbol IN ({placeholders})
        GROUP BY symbol
    """
    try:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(query, requested).fetchall()
    except sqlite3.Error as exc:
        raise RuntimeError(f"Training data validation failed reading {db_path}: {exc}") from exc

    counts = {str(symbol).upper(): int(count) for symbol, count in rows}
    insufficient = {
        symbol: counts.get(symbol, 0)
        for symbol in requested
        if counts.get(symbol, 0) < min_rows_per_symbol
    }
    if insufficient:
        detail = ",".join(f"{symbol}:{count}" for symbol, count in sorted(insufficient.items()))
        raise RuntimeError(
            "Training data validation refused insufficient per-symbol history: " + detail
        )

    return {
        "status": "OK",
        "symbols_expected": len(requested),
        "symbols_present": len(counts),
        "min_rows_per_symbol": min_rows_per_symbol,
        "min_observed_rows": min(counts.values()),
        "freshness": freshness,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate market-data coverage before retraining assessment")
    parser.add_argument("--db", default="data/market/market_data.sqlite")
    parser.add_argument("--universe", default="config/universe.yaml")
    parser.add_argument("--benchmark", default="SPY")
    parser.add_argument("--min-rows-per-symbol", type=int, default=60)
    parser.add_argument("--max-age-days", type=int, default=7)
    args = parser.parse_args()

    try:
        summary = validate_training_market_coverage(
            Path(args.db),
            Path(args.universe),
            benchmark=args.benchmark,
            min_rows_per_symbol=args.min_rows_per_symbol,
            max_age_days=args.max_age_days,
        )
    except (RuntimeError, ValueError) as exc:
        print(json.dumps({"status": "CRITICAL", "error": str(exc)}, ensure_ascii=False))
        return 1

    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
