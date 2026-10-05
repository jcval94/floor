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



def test_externally_repairable_weekly_artifact_failures_are_not_commit_circuit_broken() -> None:
    for message in (
        "Frozen Weekly challenger is missing.",
        "Frozen Weekly challenger is an unresolved LFS pointer: model.json",
        "Frozen Weekly challenger must keep canonical_serving_enabled=false",
        "Strategy League weekly model contract invalid: target_semantics",
    ):
        result = classify_failure_text(message, workflow="eod.yml")
        assert result["deterministic"] is False
        assert result["code"] is None
