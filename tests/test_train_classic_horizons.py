from __future__ import annotations

import csv
import json
from pathlib import Path

from models.train_classic_horizons import run


def _row(i: int, split: str) -> dict:
    close = 100.0 + i
    return {
        "timestamp": f"2024-01-{(i % 28) + 1:02d}T00:00:00",
        "split": split,
        "close": close,
        "floor_d1": close - 1.0,
        "ceiling_d1": close + 1.0,
        "floor_w1": close - 2.0,
        "ceiling_w1": close + 2.0,
        "floor_q1": close - 3.0,
        "ceiling_q1": close + 3.0,
        "split_eligible_d1": True,
        "split_eligible_w1": True,
        "split_eligible_q1": True,
        "floor_day_w1": (i % 5) + 1,
        "ceiling_day_w1": ((i + 1) % 5) + 1,
        "floor_day_q1": (i % 10) + 1,
        "ceiling_day_q1": ((i + 1) % 10) + 1,
    }


def test_train_classic_horizons_outputs_json_and_csv(tmp_path: Path) -> None:
    rows = [
        _row(i, "train" if i < 20 else "validation" if i < 25 else "test")
        for i in range(30)
    ]
    dataset = tmp_path / "modelable_dataset.json"
    dataset.write_text(json.dumps({"rows": rows}), encoding="utf-8")

    out_dir = tmp_path / "models"
    csv_path = run(dataset, out_dir, version="vtest")

    assert csv_path.exists()
    for horizon in ("d1", "w1", "q1"):
        artifact_path = out_dir / f"{horizon}_champion.json"
        competition_path = out_dir / f"{horizon}_competition.json"
        assert artifact_path.exists()
        assert competition_path.exists()
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        competition = json.loads(competition_path.read_text(encoding="utf-8"))
        assert payload["horizon"] == horizon
        assert payload["train_rows"] > 0
        assert payload["test_rows"] == 5  # legacy field name; these are validation rows
        assert len(competition["candidates"]) == 5
        assert payload["model_name"] in {c["model_id"] for c in competition["candidates"]}
        assert competition["selection_split"] == "validation"
        assert competition["test_used_for_selection"] is False
        assert payload["params"]["split_integrity"]["test_used_for_selection"] is False

    with csv_path.open("r", encoding="utf-8", newline="") as fh:
        rows_csv = list(csv.DictReader(fh))
    assert {row["horizon"] for row in rows_csv} == {"d1", "w1", "q1"}
