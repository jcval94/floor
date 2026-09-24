from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from contracts.model_contract import (
    build_model_contract,
    validate_model_artifact_contract,
)
from contracts.strategy_contract import (
    validate_league_strategy_members,
    validate_strategy_registry,
)
from contracts.trading import (
    load_strategy_runtime_config,
    validate_shadow_execution_contract,
)
from floor.schemas import PredictionRecord
from league.engine import advance_league, build_leaderboard, initialize_league
from models.temporal_cv import purged_chronological_calibration_split
from models.train_classic_horizons import run as run_classic_horizons
from strategies.common import geometry
from strategies.registry import STRATEGY_GENERATORS


def test_declared_classic_model_requires_risk_geometry_but_legacy_remains_readable() -> None:
    artifact = {
        "model_name": "regime_median_d1",
        "version": "v1",
        "params": {
            "schema_version": 2,
            "floor": {},
            "ceiling": {},
            "timing": {},
            "confidence_calibration": {},
        },
        "metrics": {},
    }

    legacy = validate_model_artifact_contract("d1", artifact, allow_legacy=True)
    assert legacy["valid"] is True
    assert legacy["status"] == "legacy_compatible"

    declared = dict(artifact)
    declared["model_contract"] = build_model_contract("d1")
    rejected = validate_model_artifact_contract("d1", declared, allow_legacy=True)
    assert rejected["valid"] is False
    assert "missing new-artifact params field: risk_geometry" in rejected["errors"]





def test_declared_classic_model_rejects_malformed_risk_geometry() -> None:
    artifact = {
        "model_name": "regime_median_d1",
        "version": "v2",
        "params": {
            "schema_version": 2,
            "floor": {},
            "ceiling": {},
            "timing": {},
            "confidence_calibration": {},
            "risk_geometry": {
                "method": "unknown",
                "target_marginal_coverage": 1.2,
                "floor_delta_addon": -0.1,
                "ceiling_delta_addon": 0.02,
                "calibration_rows": 0,
            },
        },
        "metrics": {},
        "model_contract": build_model_contract("d1"),
    }
    result = validate_model_artifact_contract("d1", artifact, allow_legacy=True)

    assert result["valid"] is False
    assert "risk_geometry method must be validation_residual_quantile" in result["errors"]
    assert "risk_geometry target_marginal_coverage must be in (0.5, 1)" in result["errors"]
    assert "risk_geometry floor_delta_addon must be non-negative" in result["errors"]
    assert "risk_geometry calibration_rows must be positive" in result["errors"]


def test_strategy_contract_registry_covers_registry_and_all_league_strategies() -> None:
    validate_strategy_registry(set(STRATEGY_GENERATORS))

    league_cfg = json.loads(
        Path("config/strategy_league.json").read_text(encoding="utf-8")
    )
    strategy_members = {
        str(spec["id"])
        for spec in league_cfg["members"]
        if spec.get("type") == "strategy"
    }
    validate_league_strategy_members(strategy_members)


def test_contract_defaults_are_repo_relative_not_cwd_relative(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    model_contract = build_model_contract("d1")
    strategy_cfg = load_strategy_runtime_config()

    assert model_contract["contract_id"] == "classic_range_geometry_v1"
    assert strategy_cfg["contract_meta"]["risk_scope"] == "operational"


def test_runtime_strategy_config_uses_authoritative_cost_and_risk_contracts() -> None:
    cfg = load_strategy_runtime_config()

    assert cfg["costs"]["broker_commission_bps"] == pytest.approx(2.0)
    assert cfg["costs"]["platform_fee_bps_per_side"] == pytest.approx(24.0)
    assert cfg["costs"]["slippage_bps"] == pytest.approx(3.0)
    assert cfg["portfolio"]["max_position_pct_nav"] == pytest.approx(0.08)
    assert cfg["portfolio"]["max_sector_exposure_pct_nav"] == pytest.approx(0.25)

    for strategy in cfg["strategies"].values():
        sizing = strategy["position_sizing"]
        assert float(sizing["max_weight_pct_nav"]) <= 0.08
        assert float(sizing["max_notional_usd"]) <= 50_000.0


def test_shadow_execution_config_matches_canonical_cost_profile() -> None:
    league_cfg = json.loads(
        Path("config/strategy_league.json").read_text(encoding="utf-8")
    )
    validate_shadow_execution_contract(league_cfg["execution"])


def test_prediction_schema_does_not_claim_untrained_q10_q90() -> None:
    record = PredictionRecord(
        symbol="AAA",
        as_of=datetime(2026, 9, 24, tzinfo=timezone.utc),
        event_type="CLOSE",
        horizon="d1",
        floor_value=95.0,
        ceiling_value=105.0,
    )
    assert record.floor_quantile is None
    assert record.ceiling_quantile is None
    assert record.risk_geometry_available is False


def test_strategy_geometry_uses_central_targets_and_separate_risk_boundary() -> None:
    row = {
        "close": 100.0,
        "floor_d1": 95.0,
        "ceiling_d1": 110.0,
        "risk_floor_d1": 90.0,
        "risk_ceiling_d1": 115.0,
        "risk_geometry_available_d1": True,
    }
    result = geometry(row, "d1")

    assert result["floor"] == pytest.approx(95.0)
    assert result["ceiling"] == pytest.approx(110.0)
    assert result["risk_floor"] == pytest.approx(90.0)
    assert result["risk_ceiling"] == pytest.approx(115.0)
    assert result["long_rr"] == pytest.approx(1.0)
    assert result["short_rr"] == pytest.approx(1.0 / 3.0)
    assert result["geometry_semantics"] == "central_plus_calibrated_risk"


def test_risk_calibration_split_keeps_dates_together_and_purges_crossing_labels() -> None:
    rows = [
        {
            "timestamp": "2026-01-01T20:00:00+00:00",
            "symbol": symbol,
            "target_end_date_d1": "2026-01-02",
        }
        for symbol in ("AAA", "BBB")
    ]
    rows += [
        {
            "timestamp": "2026-01-02T20:00:00+00:00",
            "symbol": symbol,
            "target_end_date_d1": "2026-01-04",
        }
        for symbol in ("AAA", "BBB")
    ]
    rows += [
        {
            "timestamp": "2026-01-03T20:00:00+00:00",
            "symbol": symbol,
            "target_end_date_d1": "2026-01-04",
        }
        for symbol in ("AAA", "BBB")
    ]
    rows += [
        {
            "timestamp": "2026-01-04T20:00:00+00:00",
            "symbol": symbol,
            "target_end_date_d1": "2026-01-05",
        }
        for symbol in ("AAA", "BBB")
    ]

    calibration, evaluation = purged_chronological_calibration_split(
        rows,
        target_end_field="target_end_date_d1",
    )

    calibration_dates = {row["timestamp"][:10] for row in calibration}
    evaluation_dates = {row["timestamp"][:10] for row in evaluation}
    assert calibration_dates == {"2026-01-01"}
    assert evaluation_dates == {"2026-01-03", "2026-01-04"}
    assert {row["symbol"] for row in calibration} == {"AAA", "BBB"}
    assert calibration_dates.isdisjoint(evaluation_dates)


def test_new_classic_training_emits_risk_geometry_and_model_contract(
    tmp_path: Path,
) -> None:
    rows = []
    for idx in range(20):
        close = 100.0 + idx * 0.1
        rows.append(
            {
                "timestamp": f"2026-01-{idx + 1:02d}T20:00:00+00:00",
                "symbol": "AAA",
                "split": "train" if idx < 12 else "validation",
                "split_eligible_d1": True,
                "close": close,
                "floor_d1": close * (0.97 - (idx % 3) * 0.001),
                "ceiling_d1": close * (1.04 + (idx % 4) * 0.001),
                "atr_14": 2.0,
                "trend_context_m3": 0.1,
            }
        )
    dataset = tmp_path / "dataset.json"
    output = tmp_path / "models"
    dataset.write_text(json.dumps({"rows": rows}), encoding="utf-8")

    run_classic_horizons(
        dataset_path=dataset,
        output_dir=output,
        version="v-contract",
        tasks=["d1"],
        model_families=["regime_median"],
    )

    artifact = json.loads(
        (output / "d1_champion.json").read_text(encoding="utf-8")
    )
    assert artifact["model_contract"]["contract_id"] == "classic_range_geometry_v1"
    assert artifact["model_contract"]["directional"] is False
    risk = artifact["params"]["risk_geometry"]
    assert risk["method"] == "validation_residual_quantile"
    assert risk["target_marginal_coverage"] == pytest.approx(0.80)
    assert risk["calibration_rows"] > 0
    assert "risk_floor_coverage" in artifact["metrics"]


def _league_cfg() -> dict:
    return {
        "league_id": "p0_contract_test",
        "initial_nav_usd": 10_000.0,
        "execution": {
            "commission_bps": 26.0,
            "slippage_bps": 3.0,
            "sell_fee_bps": 3.0,
        },
        "strategy_max_holding_sessions": {
            "weekly_opportunity_ridge": 10,
            "mean_reversion_floor_w1": 5,
            "cross_horizon_asymmetry": 10,
        },
        "members": [
            {"id": "weekly_opportunity_ridge", "type": "strategy"},
            {"id": "mean_reversion_floor_w1", "type": "strategy"},
            {"id": "cross_horizon_asymmetry", "type": "strategy"},
            {"id": "benchmark_spy", "type": "benchmark"},
        ],
        "promotion_review": {
            "min_sessions": 63,
            "min_trades": 10,
            "max_drawdown_abs": 0.15,
            "min_sharpe": 0.5,
        },
    }


def _frozen_contract() -> dict[str, str]:
    return {
        "league_config_sha256": "league",
        "strategies_config_sha256": "strategies",
        "weekly_model_sha256": "weekly",
    }


def test_league_governance_applies_to_every_strategy_member(tmp_path: Path) -> None:
    cfg = _league_cfg()
    state = initialize_league(
        tmp_path,
        cfg,
        "2026-09-01",
        _frozen_contract(),
        {
            "weekly_opportunity_ridge": {},
            "mean_reversion_floor_w1": {},
            "cross_horizon_asymmetry": {},
            "benchmark_spy": {"SPY": {"weight": 1.0}},
        },
    )
    leaderboard = build_leaderboard(state, cfg)
    rows = {
        row["strategy"]: row
        for row in leaderboard["rows"]
        if not row["strategy"].startswith("benchmark_")
    }
    assert set(rows) == {
        "weekly_opportunity_ridge",
        "mean_reversion_floor_w1",
        "cross_horizon_asymmetry",
    }
    for row in rows.values():
        assert row["promotion_checks"]["model_suite_frozen"] is False
        assert row["promotion_checks"]["min_sessions"] is False
        assert row["evaluation_variant"] == "long_only_projection"
        assert row["evidence_scope"] == "long_only"
        assert row["evidence_contract"] == "legacy_v1_model_suite_unfrozen"
        assert row["canonical_bidirectional_promotion_eligible"] is False


def test_new_league_contract_freezes_serving_model_suite(tmp_path: Path) -> None:
    cfg = _league_cfg()
    frozen = {
        **_frozen_contract(),
        "model_suite_contract_version": "v2",
        "model_contracts_sha256": "models-contract",
        "strategy_contracts_sha256": "strategies-contract",
        "d1_champion_sha256": "d1",
        "w1_champion_sha256": "w1",
        "q1_champion_sha256": "q1",
        "value_champion_sha256": "value",
        "timing_champion_sha256": "timing",
    }
    state = initialize_league(
        tmp_path,
        cfg,
        "2026-09-01",
        frozen,
        {
            "weekly_opportunity_ridge": {},
            "mean_reversion_floor_w1": {},
            "cross_horizon_asymmetry": {},
            "benchmark_spy": {"SPY": {"weight": 1.0}},
        },
    )
    leaderboard = build_leaderboard(state, cfg)
    assert leaderboard["model_suite_frozen"] is True
    assert leaderboard["evidence_contract"] == "v2_model_suite_frozen"
    strategies = [
        row
        for row in leaderboard["rows"]
        if not row["strategy"].startswith("benchmark_")
    ]
    assert all(
        row["promotion_checks"]["model_suite_frozen"] is True
        for row in strategies
    )


def test_league_stop_gap_through_fills_at_open_not_stale_stop(tmp_path: Path) -> None:
    cfg = _league_cfg()
    targets = {
        "weekly_opportunity_ridge": {
            "AAA": {
                "weight": 0.20,
                "stop_price": 80.0,
                "take_profit_price": 130.0,
            }
        },
        "mean_reversion_floor_w1": {},
        "cross_horizon_asymmetry": {},
        "benchmark_spy": {"SPY": {"weight": 1.0}},
    }
    state = initialize_league(
        tmp_path,
        cfg,
        "2026-09-01",
        _frozen_contract(),
        targets,
    )

    normal = {
        "AAA": {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
        "SPY": {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
    }
    state = advance_league(
        tmp_path,
        state,
        cfg,
        "2026-09-02",
        normal,
        _frozen_contract(),
        {},
    )
    assert "AAA" in state["members"]["weekly_opportunity_ridge"]["positions"]

    gap = {
        "AAA": {"open": 70.0, "high": 75.0, "low": 68.0, "close": 72.0},
        "SPY": {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
    }
    advance_league(
        tmp_path,
        state,
        cfg,
        "2026-09-03",
        gap,
        _frozen_contract(),
        {},
    )

    records = [
        json.loads(line)
        for line in (tmp_path / "history.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    sell = next(
        trade
        for trade in records[-1]["trades"]
        if trade["member"] == "weekly_opportunity_ridge"
        and trade["symbol"] == "AAA"
        and trade["side"] == "SELL"
    )
    assert sell["reason"] == "stop_gap_through_at_open"
    assert sell["raw_price"] == pytest.approx(70.0)
