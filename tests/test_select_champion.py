from __future__ import annotations

import json
from pathlib import Path

from models.select_champion import select_and_persist_champion


def _value_payload(version: str = "v-test") -> dict:
    return {
        "model_name": "m3_value_linear",
        "horizon": "m3",
        "target": "floor_m3",
        "version": version,
        "params": {"weights": {"feature_a": 1.0}, "bias": 95.0},
        "metrics": {
            "pinball_loss": 0.1,
            "mae_realized_floor": 0.2,
            "breach_rate": 0.2,
            "calibration_error": 0.05,
            "temporal_stability": 0.9,
        },
    }


def test_select_and_persist_champion_handles_empty_existing_json(tmp_path: Path) -> None:
    models_dir = tmp_path / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    champion_path = models_dir / "value_champion.json"
    champion_path.write_text("", encoding="utf-8")

    result = select_and_persist_champion(_value_payload(), models_dir, task="value")

    assert result["decision"] == "promote_first"
    persisted = json.loads(champion_path.read_text(encoding="utf-8"))
    assert isinstance(persisted, dict)
    assert persisted["selection"]["decision"] == "promote_first"


def test_select_and_persist_champion_handles_corrupt_existing_json(tmp_path: Path) -> None:
    models_dir = tmp_path / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    champion_path = models_dir / "value_champion.json"
    champion_path.write_text("{bad", encoding="utf-8")

    result = select_and_persist_champion(_value_payload(), models_dir, task="value")

    assert result["decision"] == "promote_first"
    persisted = json.loads(champion_path.read_text(encoding="utf-8"))
    assert persisted["selection"]["decision"] == "promote_first"


def test_select_and_persist_champion_handles_non_dict_existing_json_payload(tmp_path: Path) -> None:
    models_dir = tmp_path / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    champion_path = models_dir / "value_champion.json"
    champion_path.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")

    result = select_and_persist_champion(_value_payload(), models_dir, task="value")

    assert result["decision"] == "promote_first"
    persisted = json.loads(champion_path.read_text(encoding="utf-8"))
    assert persisted["selection"]["decision"] == "promote_first"


def test_select_and_persist_champion_atomic_writes_leave_parseable_json_files(tmp_path: Path) -> None:
    models_dir = tmp_path / "models"
    result = select_and_persist_champion(_value_payload(), models_dir, task="value")

    champion_payload = json.loads(Path(result["champion_path"]).read_text(encoding="utf-8"))
    challenger_payload = json.loads(Path(result["challenger_path"]).read_text(encoding="utf-8"))

    assert champion_payload["version"] == "v-test"
    assert challenger_payload["selection"]["decision"] == "promote_first"
