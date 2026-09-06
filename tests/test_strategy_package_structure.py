from __future__ import annotations

from pathlib import Path

from strategies.registry import ACTIVE_STRATEGY_IDS
from strategies.run_strategies import load_simple_yaml


ROOT = Path(__file__).resolve().parents[1]
STRATEGIES_ROOT = ROOT / "src" / "strategies"


def test_every_active_strategy_has_its_own_package() -> None:
    config = load_simple_yaml(ROOT / "config" / "strategies.yaml")
    configured_ids = set(config["strategies"])

    assert set(ACTIVE_STRATEGY_IDS) == configured_ids
    for strategy_id in ACTIVE_STRATEGY_IDS:
        package = STRATEGIES_ROOT / strategy_id
        assert package.is_dir(), f"missing package for {strategy_id}"
        assert (package / "__init__.py").is_file()
        assert (package / "strategy.py").is_file()


def test_root_compatibility_modules_contain_no_strategy_logic() -> None:
    compatibility_modules = [
        "strategy_pack_v2.py",
        "strategy_breakout_floor.py",
        "strategy_mean_reversion.py",
        "strategy_weekly_opportunity.py",
    ]
    for filename in compatibility_modules:
        text = (STRATEGIES_ROOT / filename).read_text(encoding="utf-8")
        assert "\ndef generate_" not in text
        assert "StrategyDecision(" not in text


def test_retired_directional_implementations_are_not_in_active_source_root() -> None:
    for filename in (
        "strategy_ai_only.py",
        "strategy_model_only.py",
        "strategy_consensus.py",
    ):
        assert not (STRATEGIES_ROOT / filename).exists()
