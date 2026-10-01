from pathlib import Path

import pytest

from league.freeze import verify_challenger_freeze


def test_current_challenger_matches_frozen_v1() -> None:
    result = verify_challenger_freeze(
        Path("config/strategy_league.json"),
        Path("config/frozen/capital_challenger_v1_v11.json"),
    )
    assert result["status"] == "FROZEN_OK"
    assert result["freeze_id"] == "capital_challenger_v1_refrozen_20261001_v11"


def test_freeze_rejects_parameter_mutation(tmp_path: Path) -> None:
    league = Path("config/strategy_league.json").read_text(encoding="utf-8")
    mutated = league.replace('"quality_floor": 0.55', '"quality_floor": 0.56')
    path = tmp_path / "league.json"
    path.write_text(mutated, encoding="utf-8")
    with pytest.raises(RuntimeError, match="parameters changed after freeze"):
        verify_challenger_freeze(path, Path("config/frozen/capital_challenger_v1_v11.json"))


def test_epoch_registry_and_genesis_request_are_consistent() -> None:
    import json

    league = json.loads(Path("config/strategy_league.json").read_text(encoding="utf-8"))
    epochs = json.loads(
        Path("config/strategy_league_epochs.json").read_text(encoding="utf-8")
    )
    request = json.loads(
        Path("config/strategy_league_genesis_request.json").read_text(encoding="utf-8")
    )
    assert epochs["current_league_id"] == league["league_id"]
    current = next(
        item for item in epochs["epochs"] if item["league_id"] == league["league_id"]
    )
    assert request["league_id"] == league["league_id"]
    assert request["freeze_id"] == current["freeze_id"]
    assert f"/{league['league_id']}/" in league["weekly_model_path"]

    frozen = json.loads(
        Path("config/frozen/capital_challenger_v1_v11.json").read_text(encoding="utf-8")
    )
    assert frozen["freeze_id"] == current["freeze_id"]
    assert frozen["source_league_id"] == league["league_id"]
    assert frozen["challenger_config"] == league["capital_allocation_challenger"]


def test_previous_epochs_and_freezes_remain_preserved() -> None:
    import json

    epochs = json.loads(
        Path("config/strategy_league_epochs.json").read_text(encoding="utf-8")
    )
    by_id = {item["league_id"]: item for item in epochs["epochs"]}
    assert "strategy_league_v7_clean_genesis_10k" in by_id
    assert "strategy_league_v8_net_alpha_10k" in by_id
    assert "strategy_league_v9_net_target_reversal_10k" in by_id
    assert "strategy_league_v10_d1_w1_champions_10k" in by_id
    assert by_id["strategy_league_v9_net_target_reversal_10k"]["status"] == (
        "CLOSED_SERVING_MODEL_SUITE_CHANGE"
    )
    assert by_id["strategy_league_v10_d1_w1_champions_10k"]["status"] == (
        "CLOSED_ENTRY_SEMANTICS_AND_CADENCE_CHANGE"
    )
    for suffix in ("v7", "v8", "v9", "v10", "v11"):
        assert Path(f"config/frozen/capital_challenger_v1_{suffix}.json").exists()
