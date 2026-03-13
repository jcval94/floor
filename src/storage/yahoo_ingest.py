from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from floor.universe import parse_universe_yaml
from storage.market_db import DailyBar, init_market_db, upsert_daily_bars

logger = logging.getLogger(__name__)


def _to_iso_utc(epoch_seconds: int) -> str:
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).isoformat()


def fetch_yahoo_chart(symbol: str, range_: str, interval: str) -> dict:
    base = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    query = urlencode({"range": range_, "interval": interval, "events": "div,splits"})
    req = Request(
        f"{base}?{query}",
        headers={"User-Agent": "floor-bot/1.0 (research; responsible polling)"},
    )
    logger.info("[etl:yahoo] requesting symbol=%s range=%s interval=%s", symbol, range_, interval)
    with urlopen(req, timeout=30) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def parse_daily_bars(symbol: str, payload: dict) -> list[DailyBar]:
    result = (payload.get("chart", {}).get("result") or [{}])[0]
    timestamps = result.get("timestamp") or []
    quotes = (result.get("indicators", {}).get("quote") or [{}])[0]

    opens = quotes.get("open") or []
    highs = quotes.get("high") or []
    lows = quotes.get("low") or []
    closes = quotes.get("close") or []
    volumes = quotes.get("volume") or []

    bars: list[DailyBar] = []
    for idx, ts in enumerate(timestamps):
        try:
            o = opens[idx] if idx < len(opens) else None
            h = highs[idx] if idx < len(highs) else None
            low_value = lows[idx] if idx < len(lows) else None
            c = closes[idx] if idx < len(closes) else None
            v = volumes[idx] if idx < len(volumes) else None
            if None in (o, h, low_value, c, v):
                continue
            bars.append(
                DailyBar(
                    symbol=symbol.upper(),
                    ts_utc=_to_iso_utc(int(ts)),
                    open=float(o),
                    high=float(h),
                    low=float(low_value),
                    close=float(c),
                    volume=float(v),
                )
            )
        except (TypeError, ValueError) as exc:
            logger.warning("[etl:yahoo] bad bar skipped symbol=%s idx=%s error=%s", symbol, idx, exc)
    logger.info("[etl:yahoo] parsed bars symbol=%s count=%s", symbol, len(bars))
    if bars:
        logger.info("[etl:yahoo] sample bars symbol=%s first=%s last=%s", symbol, bars[0], bars[-1])
    return bars


def ingest_yahoo_to_db(
    db_path: Path,
    symbols: list[str],
    range_: str = "2y",
    interval: str = "1d",
    sleep_seconds: float = 0.4,
) -> dict:
    logger.info("[etl:yahoo] start ingest symbols=%s db=%s", len(symbols), db_path)
    try:
        init_market_db(db_path)
    except Exception as exc:
        logger.exception("[etl:yahoo] failed initializing market db path=%s error=%s", db_path, exc)
        raise

    inserted = 0
    failed: list[dict] = []

    for symbol in symbols:
        attempts = 0
        ok = False
        while attempts < 3 and not ok:
            attempts += 1
            try:
                payload = fetch_yahoo_chart(symbol, range_=range_, interval=interval)
                bars = parse_daily_bars(symbol, payload)
                upserted = upsert_daily_bars(db_path, bars, raw_payload=None)
                inserted += upserted
                logger.info("[etl:yahoo] upserted symbol=%s rows=%s attempt=%s", symbol, upserted, attempts)
                ok = True
            except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
                logger.warning(
                    "[etl:yahoo] recoverable error symbol=%s attempt=%s error=%s",
                    symbol,
                    attempts,
                    exc,
                )
                if attempts >= 3:
                    failed.append({"symbol": symbol, "error": str(exc)})
                time.sleep(min(2.0, attempts * sleep_seconds))
            except Exception as exc:
                logger.exception("[etl:yahoo] non-recoverable error symbol=%s error=%s", symbol, exc)
                failed.append({"symbol": symbol, "error": str(exc)})
                break
        time.sleep(sleep_seconds)

    summary = {"symbols": len(symbols), "upserted_rows": inserted, "failed": failed, "db_path": str(db_path)}
    logger.info("[etl:yahoo] finished ingest summary=%s", summary)
    return summary


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    parser = argparse.ArgumentParser(description="Ingest Yahoo daily bars into in-repo SQLite")
    parser.add_argument("--db", default="data/market/market_data.sqlite", help="SQLite path")
    parser.add_argument("--universe", default="config/universe.yaml", help="Universe yaml path")
    parser.add_argument("--benchmark", default="SPY", help="Add benchmark symbol")
    parser.add_argument("--range", default="2y", help="Yahoo range (e.g., 1y,2y,5y)")
    parser.add_argument("--interval", default="1d", help="Yahoo interval (e.g., 1d)")
    parser.add_argument("--sleep-seconds", type=float, default=0.4, help="Delay between requests")
    args = parser.parse_args()

    try:
        symbols = parse_universe_yaml(Path(args.universe))
        benchmark = args.benchmark.strip().upper()
        if benchmark and benchmark not in symbols:
            symbols.append(benchmark)

        result = ingest_yahoo_to_db(
            db_path=Path(args.db),
            symbols=symbols,
            range_=args.range,
            interval=args.interval,
            sleep_seconds=args.sleep_seconds,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as exc:
        logger.exception("[etl:yahoo] fatal error in CLI: %s", exc)
        raise


if __name__ == "__main__":
    main()
