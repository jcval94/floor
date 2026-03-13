from __future__ import annotations

import json
from pathlib import Path

from floor.main import _load_universe_symbols
from floor.reporting.generate_site_data import build_dashboard_snapshot


def test_build_dashboard_snapshot_keeps_all_prediction_files(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    pred_dir = data_dir / "predictions"
    sig_dir = data_dir / "signals"
    pred_dir.mkdir(parents=True)
    sig_dir.mkdir(parents=True)

    for i in range(25):
        symbol = f"SYM{i:02d}"
        row = {
            "symbol": symbol,
            "horizon": "q1",
            "floor_value": 100 + i,
            "ceiling_value": 110 + i,
        }
        (pred_dir / f"{symbol}.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
        (sig_dir / f"{symbol}.jsonl").write_text(json.dumps({"symbol": symbol, "action": "HOLD"}) + "\n", encoding="utf-8")

    out = data_dir / "reports" / "dashboard.json"
    build_dashboard_snapshot(data_dir, output_path=out)

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["prediction_files"] == 25
    assert payload["signal_files"] == 25
    assert len(payload["latest_predictions"]) == 25


def test_load_universe_symbols_parses_config_list(tmp_path: Path) -> None:
    universe = tmp_path / "universe.yaml"
    universe.write_text(
        """
universe:
  name: test
  symbols:
    - AAPL
    - msft
""".strip()
        + "\n",
        encoding="utf-8",
    )

    assert _load_universe_symbols(universe) == ["AAPL", "MSFT"]
