from utils.recovery_failure_policy import classify_failure_text


def test_material_shadow_divergence_is_deterministic() -> None:
    result = classify_failure_text(
        "RuntimeError: Material shadow/EOD divergence: 36",
        workflow="eod.yml",
    )
    assert result["deterministic"] is True
    assert result["code"] == "shadow_eod_divergence"


def test_transient_market_failure_is_not_circuit_broken() -> None:
    result = classify_failure_text(
        "Yahoo Finance timeout while fetching recent daily bars",
        workflow="eod.yml",
    )
    assert result["deterministic"] is False
    assert result["code"] is None


def test_non_eod_workflow_is_not_classified_by_eod_policy() -> None:
    result = classify_failure_text(
        "RuntimeError: Material shadow/EOD divergence: 36",
        workflow="intraday_engine.yml",
    )
    assert result["deterministic"] is False


def test_known_weekly_contract_failures_are_deterministic() -> None:
    cases = {
        "weekly_model_missing": "Frozen Weekly challenger is missing.",
        "weekly_model_lfs_pointer": (
            "Frozen Weekly challenger is an unresolved LFS pointer: model.json"
        ),
        "weekly_model_serving_mode_invalid": (
            "Frozen Weekly challenger must keep canonical_serving_enabled=false"
        ),
        "weekly_model_contract_invalid": (
            "Strategy League weekly model contract invalid: target_semantics"
        ),
        "weekly_model_serving_contract_invalid": (
            "Strategy League refuses Weekly artifact unless "
            "canonical_serving_enabled=false"
        ),
    }
    for expected_code, message in cases.items():
        result = classify_failure_text(message, workflow="eod.yml")
        assert result["deterministic"] is True
        assert result["code"] == expected_code
