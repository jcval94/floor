from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuntimeConfig:
    root_dir: Path = Path(".")
    data_dir: Path = Path("data")
    recommendations_csv_url: str | None = None
    live_trading_enabled: bool = False

    @staticmethod
    def from_env() -> "RuntimeConfig":
        return RuntimeConfig(
            root_dir=Path(os.getenv("FLOOR_ROOT_DIR", ".")),
            data_dir=Path(os.getenv("FLOOR_DATA_DIR", "data")),
            recommendations_csv_url=os.getenv("GOOGLE_SHEETS_RECOMMENDATIONS_CSV_URL"),
            live_trading_enabled=os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "true",
        )
