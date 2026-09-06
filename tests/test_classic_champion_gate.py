from __future__ import annotations

import json
from pathlib import Path

from models.classic_champion_gate import gate_one_horizon
from models.horizon_timing import ALLOWED_CLASSES


def _dataset(path: Path) -> None:
    rows = []
    for idx in range(12):
        close = 100.0
        rows.append(
            {
                "timestamp": f"2026-01-{idx + 1:02d}T20:00:00+00:00",
                "symbol": "AAA",
                "split": "train" if idx < 8 else "validation",
                "split_eligible_d1": True,
                "close": close,
                "floor_d1": 98.0,
                "ceiling_d1": 103.0,
                "atr_14": 2.0,
                "trend_context_m3": 0.1,
            }
        )
    path.write_text(json.dumps({"rows": rows}), encoding="utf-8")


def _artifact(
    *,
    version: str,
    floor_delta: float,
    ceiling_delta: float,
    model_name: str = "regime_median_d1",
) -> dict:
    return {
        "horizon": "d1",
        "model_name": model_name,
        "version": version,
        "floor_delta": floor_delta,
        "ceiling_delta": ceiling_delta,
        "train_rows": 8,
        "test_rows": 4,
        "metrics": {
            "mae_floor_pct": 0.0,
            "mae_ceiling_pct": 0.0,
            "mae_spread_pct": 0.0,
        },
        "params": {
            "schema_version": 2,
            "floor": {
                "global": floor_delta,
                "table": {},
                "vol_cuts": [0.01, 0.03],
                "bins": 3,
            },
            "ceiling": {
                "global": ceiling_delta,
                "table": {},
                "vol_cuts": [0.01, 0.03],
                "bins": 3,
            },
            "timing": {
                "schema_version": 2,
                "horizon": "d1",
                "status": "unavailable_daily_resolution",
                "classes": list(ALLOWED_CLASSES["d1"]),
                "train_rows": 0,
                "vol_cuts": [],
                "floor": {"rows": 0, "global": {}, "table": {}},
                "ceiling": {"rows": 0, "global": {}, "table": {}},
            },
            "confidence_calibration": {
                "method": "validation_empirical_interval_breach",
                "breach_probability": 0.20,
                "evaluation_rows": 4,
            },
        },
    }


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_classic_gate_keeps_historical_champion_when_candidate_is_worse(
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "dataset.json"
    registry = tmp_path / "registry"
    previous = tmp_path / "previous"
    _dataset(dataset)

    old = _artifact(version="old", floor_delta=0.02, ceiling_delta=0.03)
    worse = _artifact(version="new", floor_delta=0.20, ceiling_delta=0.20)
    _write(previous / "d1_champion.json", old)
    _write(registry / "d1_champion.json", worse)

    result = gate_one_horizon(
        dataset_path=dataset,
        registry_dir=registry,
        previous_dir=previous,
        horizon="d1",
        version="new",
    )

    assert result["decision"] == "challenger_only"
    active = json.loads((registry / "d1_champion.json").read_text(encoding="utf-8"))
    assert active["version"] == "old"
    challengers = list(registry.glob("d1_challenger_new.json"))
    assert len(challengers) == 1
    competition = json.loads(
        (registry / "d1_competition.json").read_text(encoding="utf-8")
    )
    assert competition["registry_decision"] == "challenger_only"
    assert competition["test_used_for_promotion"] is False


def test_classic_gate_rejects_spread_win_when_one_boundary_regresses(
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "dataset.json"
    registry = tmp_path / "registry"
    previous = tmp_path / "previous"
    _dataset(dataset)

    # Actual deltas are floor=.02, ceiling=.03.  The candidate has a better
    # spread but damages the previously exact floor, so Pareto promotion must fail.
    old = _artifact(version="old", floor_delta=0.02, ceiling_delta=0.05)
    candidate = _artifact(version="new", floor_delta=0.03, ceiling_delta=0.03)
    _write(previous / "d1_champion.json", old)
    _write(registry / "d1_champion.json", candidate)

    result = gate_one_horizon(
        dataset_path=dataset,
        registry_dir=registry,
        previous_dir=previous,
        horizon="d1",
        version="new",
    )

    assert result["decision"] == "challenger_only"
    active = json.loads((registry / "d1_champion.json").read_text(encoding="utf-8"))
    assert active["version"] == "old"


def test_classic_gate_migrates_incompatible_legacy_champion(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset.json"
    registry = tmp_path / "registry"
    previous = tmp_path / "previous"
    _dataset(dataset)

    legacy = _artifact(
        version="legacy",
        floor_delta=0.02,
        ceiling_delta=0.03,
        model_name="evt_cp_d1",
    )
    candidate = _artifact(version="new", floor_delta=0.02, ceiling_delta=0.03)
    _write(previous / "d1_champion.json", legacy)
    _write(registry / "d1_champion.json", candidate)

    result = gate_one_horizon(
        dataset_path=dataset,
        registry_dir=registry,
        previous_dir=previous,
        horizon="d1",
        version="new",
    )

    assert result["decision"] == "promote_schema_migration"
    active = json.loads((registry / "d1_champion.json").read_text(encoding="utf-8"))
    assert active["version"] == "new"
    assert active["selection"]["test_used_for_selection"] is False
    archived = list(registry.glob("d1_champion_archived_legacy.json"))
    assert len(archived) == 1


def test_all_commit_capable_classic_training_routes_through_historical_gate() -> None:
    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts" / "retrain_models.sh").read_text(encoding="utf-8")
    workflow = (
        root / ".github" / "workflows" / "manual_train_all_models.yml"
    ).read_text(encoding="utf-8")

    assert "models.classic_champion_gate" in script
    assert "PREVIOUS_DIR" in script
    assert "bash scripts/retrain_models.sh" in workflow
    assert "python -m models.train_classic_horizons" not in workflow
