from pathlib import Path

import pytest

from league.freeze import verify_challenger_freeze


def test_current_challenger_matches_frozen_v1() -> None:
    result = verify_challenger_freeze(
        Path("config/strategy_league.json"),
        Path("config/frozen/capital_challenger_v1.json"),
    )
    assert result["status"] == "FROZEN_OK"
    assert result["freeze_id"] == "capital_challenger_v1_frozen_20260906"


def test_freeze_rejects_parameter_mutation(tmp_path: Path) -> None:
    league = Path("config/strategy_league.json").read_text(encoding="utf-8")
    mutated = league.replace('"quality_floor": 0.55', '"quality_floor": 0.56')
    path = tmp_path / "league.json"
    path.write_text(mutated, encoding="utf-8")
    with pytest.raises(RuntimeError, match="parameters changed after freeze"):
        verify_challenger_freeze(path, Path("config/frozen/capital_challenger_v1.json"))
