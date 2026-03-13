from __future__ import annotations

from pathlib import Path

DEFAULT_UNIVERSE_50 = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "AMD", "ORCL",
    "CRM", "ADBE", "CSCO", "QCOM", "AMAT", "TXN", "NFLX", "INTC", "IBM", "INTU",
    "PLTR", "JPM", "BAC", "WFC", "GS", "MS", "V", "MA", "AXP", "BRK.B",
    "LLY", "ABBV", "UNH", "MRK", "ABT", "TMO", "XOM", "CVX", "CAT", "GE",
    "RTX", "BA", "WMT", "COST", "HD", "PG", "KO", "MCD", "DIS", "UBER",
]


def parse_universe_yaml(path: Path) -> list[str]:
    if not path.exists():
        return DEFAULT_UNIVERSE_50

    symbols: list[str] = []
    symbols_indent: int | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()

        if line.startswith("symbols:"):
            symbols_indent = indent
            continue

        if symbols_indent is None:
            continue

        if indent <= symbols_indent and not line.startswith("-"):
            break

        if line.startswith("-"):
            ticker = line[1:].strip().upper()
            if ticker:
                symbols.append(ticker)

    return symbols or DEFAULT_UNIVERSE_50
